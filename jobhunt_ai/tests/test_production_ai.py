"""Production routing, cross-worker allowances, and optional-package isolation."""

from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import openai
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db import DatabaseError, connections
from django.test import RequestFactory, SimpleTestCase, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from pydantic import BaseModel

from accounts.models import Profile
from accounts.testing import make_user
from rls.testing import app_role
from jobhunt_ai.access import has_copilot_access
from jobhunt_ai.adapters import get_ai_port
from jobhunt_ai.adapters.anthropic import AnthropicAdapter
from jobhunt_ai.adapters.azure_openai import AzureOpenAIAdapter, ModelRouter, _configured_client
from jobhunt_ai.adapters.quota_store import DjangoAIQuotaStore
from jobhunt_ai.agents.schemas import ParsedProfile, MatchVerdict, ScrapedOffers, ScoutQueries
from jobhunt_ai.models import AgentRun, RunKind, RunStatus, UserAIQuota
from jobhunt_ai.ports import AIPort, LLMError, LLMRefusal, StructuredResult
from jobhunt_ai.quotas import ProductionQuotaProxy, QuotaExceededException
from jobhunt_ai.services import llm, runner
from jobhunt_ai.services import fanout
from jobhunt_ai.tests.test_fanout import FanoutFixtures
from jobhunt_ai.settings import is_saas_production
from jobhunt_ai.views import premium_required


class Answer(BaseModel):
    text: str


NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


class AzureAdapterTests(SimpleTestCase):
    def test_real_sdk_sends_azure_deployment_and_validates_structured_output(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={
                "id": "test", "object": "chat.completion", "created": 0, "model": "gpt-4o",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": '{"text":"valid"}',
                }}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
            })

        with openai.AzureOpenAI(
            azure_endpoint="https://example.openai.azure.com", api_key="test-key",
            api_version="2024-10-21", max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        ) as client:
            adapter = AzureOpenAIAdapter(client=client, router=ModelRouter("mini", "deep"))
            result = adapter.parse_structured(Answer, system="test", content="test", max_tokens=50)
        self.assertIsInstance(adapter, AIPort)
        self.assertEqual(result, StructuredResult(Answer(text="valid"), 12, 5, "deep"))
        self.assertIn("/deployments/deep/chat/completions", str(requests[0].url))
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertEqual(payload["max_completion_tokens"], 50)

    def test_router_uses_schema_identity_and_defaults_to_complex(self):
        router = ModelRouter("mini-deployment", "deep-deployment")
        for schema in (ParsedProfile, ScrapedOffers, ScoutQueries):
            self.assertEqual(router.route(schema), "mini-deployment")
        for schema in (MatchVerdict, Answer, type("ParsedProfile", (BaseModel,), {})):
            self.assertEqual(router.route(schema), "deep-deployment")

    def test_refusal_and_malformed_output_have_safe_errors(self):
        client = Mock()
        adapter = AzureOpenAIAdapter(client=client)
        for choice, expected in (
            (SimpleNamespace(finish_reason="stop", message=SimpleNamespace(refusal="private", parsed=None)), LLMRefusal),
            (SimpleNamespace(finish_reason="length", message=SimpleNamespace(refusal=None, parsed=None)), LLMError),
        ):
            client.chat.completions.parse.return_value.choices = [choice]
            with self.assertRaises(expected):
                adapter.parse_structured(Answer, system="test", content="test")

    def test_auth_errors_are_not_quota_exceeded_and_do_not_leak_credentials(self):
        client = Mock()
        client.chat.completions.parse.side_effect = openai.AuthenticationError(
            "secret-key", response=httpx.Response(401, request=httpx.Request("POST", "https://example.com")), body=None,
        )
        with self.assertRaises(LLMError) as caught:
            AzureOpenAIAdapter(client=client).parse_structured(Answer, system="s", content="c")
        self.assertNotIsInstance(caught.exception, QuotaExceededException)
        self.assertNotIn("secret-key", str(caught.exception))

    @override_settings(JOBHUNT_AI_AZURE_MAX_INPUT_CHARS=5)
    def test_large_or_binary_input_never_reaches_azure(self):
        client = Mock()
        for content in ("too long", [{"type": "document", "source": "raw pdf"}]):
            with self.assertRaises(LLMError):
                AzureOpenAIAdapter(client=client).parse_structured(Answer, system="s", content=content)
        client.chat.completions.parse.assert_not_called()

    def test_managed_identity_and_disabled_sdk_retries(self):
        _configured_client.cache_clear()
        self.addCleanup(_configured_client.cache_clear)
        with patch("azure.identity.DefaultAzureCredential") as credential, patch(
            "azure.identity.get_bearer_token_provider", return_value="token-provider"
        ) as token, patch("openai.AzureOpenAI") as sdk:
            _configured_client("https://example.openai.azure.com", "", "2024-10-21", 10)
        credential.assert_called_once()
        token.assert_called_once()
        self.assertEqual(sdk.call_args.kwargs["azure_ad_token_provider"], "token-provider")
        self.assertEqual(sdk.call_args.kwargs["max_retries"], 0)


class FactoryAndProxyTests(SimpleTestCase):
    @override_settings(IS_SAAS_PRODUCTION=False)
    def test_nonproduction_uses_default_without_azure_or_quota_store(self):
        with patch("jobhunt_ai.adapters.azure_openai.AzureOpenAIAdapter") as azure, patch(
            "jobhunt_ai.adapters.quota_store.DjangoAIQuotaStore"
        ) as quotas:
            self.assertIsInstance(get_ai_port(), AnthropicAdapter)
        azure.assert_not_called()
        quotas.assert_not_called()

    @override_settings(IS_SAAS_PRODUCTION=True)
    def test_production_composition_and_mode_changes(self):
        port = get_ai_port()
        self.assertIsInstance(port, ProductionQuotaProxy)
        assert isinstance(port, ProductionQuotaProxy)
        self.assertIsInstance(port.wrapped, AzureOpenAIAdapter)
        self.assertIsInstance(port, AIPort)
        with override_settings(IS_SAAS_PRODUCTION=False):
            self.assertIsInstance(get_ai_port(), AnthropicAdapter)

    def test_existing_proxy_bypasses_limits_when_not_production(self):
        quotas, wrapped = Mock(), Mock()
        proxy = ProductionQuotaProxy(wrapped, quotas, enabled=is_saas_production)
        with override_settings(IS_SAAS_PRODUCTION=False):
            proxy.parse_structured(Answer, system="s", content="c")
        quotas.consume.assert_not_called()
        wrapped.parse_structured.assert_called_once()

    def test_missing_owner_and_storage_failure_fail_closed(self):
        quotas, wrapped = Mock(), Mock()
        proxy = ProductionQuotaProxy(wrapped, quotas, enabled=lambda: True)
        with self.assertRaises(ValueError):
            proxy.parse_structured(Answer, system="s", content="c")
        quotas.consume.side_effect = DatabaseError("offline")
        with self.assertRaises(DatabaseError):
            proxy.parse_structured(Answer, system="s", content="c", owner_id=1)
        wrapped.parse_structured.assert_not_called()

    @override_settings(IS_SAAS_PRODUCTION="False")
    def test_string_mode_is_rejected_instead_of_bypassing(self):
        with self.assertRaises(ImproperlyConfigured):
            get_ai_port()


@override_settings(
    IS_SAAS_PRODUCTION=True, JOBHUNT_AI_FREE_MONTHLY_REQUESTS=2,
    JOBHUNT_AI_FREE_REQUESTS_PER_MINUTE=2, JOBHUNT_AI_PAID_MONTHLY_REQUESTS=4,
    JOBHUNT_AI_PAID_REQUESTS_PER_MINUTE=4, JOBHUNT_AI_UPGRADE_URL="/upgrade/",
)
class ProductionQuotaTests(TestCase):
    def setUp(self):
        self.user = make_user("Quota user")
        self.store = DjangoAIQuotaStore()
        self.provider = Mock()
        self.provider.parse_structured.return_value = StructuredResult(Answer(text="ok"), 10, 5, "mini")
        self.proxy = ProductionQuotaProxy(self.provider, self.store, enabled=is_saas_production)
        self.clock = patch("jobhunt_ai.adapters.quota_store.timezone.now", return_value=NOW).start()
        self.addCleanup(patch.stopall)

    def call(self, owner_id=None):
        return self.proxy.parse_structured(Answer, system="s", content="c", owner_id=owner_id or self.user.pk)

    def test_free_limit_denies_before_provider_and_keeps_count(self):
        self.call()
        self.call()
        with self.assertRaises(QuotaExceededException) as caught:
            self.call()
        self.assertEqual(caught.exception.period, "month")
        self.assertTrue(caught.exception.as_dict()["can_upgrade"])
        self.assertEqual(self.provider.parse_structured.call_count, 2)
        self.assertEqual(UserAIQuota.objects.get(owner=self.user).requests, 2)

    def test_paid_status_is_rechecked_and_expiry_downgrades_immediately(self):
        Profile.objects.filter(user=self.user).update(premium_until=NOW + timedelta(days=20))
        for _ in range(3):
            self.call()
        Profile.objects.filter(user=self.user).update(premium_until=NOW)
        with self.assertRaises(QuotaExceededException) as caught:
            self.call()
        self.assertEqual(caught.exception.tier, "free")

    def test_subscription_upgrade_does_not_reset_usage(self):
        self.call()
        self.call()
        Profile.objects.filter(user=self.user).update(premium_until=NOW + timedelta(days=20))
        self.call()
        self.call()
        with self.assertRaises(QuotaExceededException) as caught:
            self.call()
        self.assertFalse(caught.exception.as_dict()["can_upgrade"])
        self.assertEqual(UserAIQuota.objects.get(owner=self.user).requests, 4)

    @override_settings(JOBHUNT_AI_FREE_MONTHLY_REQUESTS=10, JOBHUNT_AI_FREE_REQUESTS_PER_MINUTE=1)
    def test_minute_and_month_resets_and_user_isolation(self):
        self.call()
        with self.assertRaises(QuotaExceededException) as caught:
            self.call()
        self.assertEqual(caught.exception.period, "minute")
        self.assertFalse(caught.exception.as_dict()["can_upgrade"])
        other = make_user("Other quota user")
        self.call(other.pk)
        self.clock.return_value = NOW + timedelta(minutes=1)
        self.call()
        self.assertEqual(UserAIQuota.objects.get(owner=self.user).requests, 2)
        self.clock.return_value = datetime(2026, 10, 1, tzinfo=UTC)
        self.call()
        self.assertEqual(UserAIQuota.objects.filter(owner=self.user).count(), 2)

    def test_failed_attempt_is_charged_and_inactive_user_cannot_call(self):
        self.provider.parse_structured.side_effect = LLMError("timeout")
        with self.assertRaises(LLMError):
            self.call()
        self.assertEqual(UserAIQuota.objects.get(owner=self.user).requests, 1)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            self.call()
        self.assertEqual(self.provider.parse_structured.call_count, 1)

    @override_settings(AUTH_MODE="accounts")
    def test_freemium_access_is_production_only(self):
        self.assertTrue(has_copilot_access(self.user))
        with override_settings(IS_SAAS_PRODUCTION=False):
            self.assertFalse(has_copilot_access(self.user))

    def test_run_usage_is_recorded_with_routed_deployment(self):
        run = AgentRun.objects.create(owner=self.user, kind=RunKind.PARSE_CV)
        with patch.object(llm, "get_ai_port", return_value=self.proxy):
            llm.parse_structured(Answer, system="s", content="c", owner_id=self.user.pk, run_id=run.pk)
        run.refresh_from_db()
        self.assertEqual((run.input_tokens, run.output_tokens, run.model_id), (10, 5, "mini"))

    def test_refused_output_keeps_reported_usage_and_request_reservation(self):
        run = AgentRun.objects.create(owner=self.user, kind=RunKind.PARSE_CV)
        self.provider.parse_structured.side_effect = LLMRefusal(
            "refused", input_tokens=8, output_tokens=2, model_id="mini",
        )
        with patch.object(llm, "get_ai_port", return_value=self.proxy):
            with self.assertRaises(LLMRefusal):
                llm.parse_structured(Answer, system="s", content="c", owner_id=self.user.pk, run_id=run.pk)
        run.refresh_from_db()
        self.assertEqual((run.input_tokens, run.output_tokens, run.model_id), (8, 2, "mini"))
        self.assertEqual(UserAIQuota.objects.get(owner=self.user).requests, 1)

    def test_worker_failure_becomes_swappable_upgrade_fragment(self):
        run = runner.launch(RunKind.SCOUT, owner=self.user)
        error = QuotaExceededException(tier="free", limit=2, period="month", reset_at=NOW)
        with patch.object(runner, "_dispatch", side_effect=error):
            runner.execute(run.pk, self.user.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertEqual(run.result["quota"]["code"], "ai_quota_exceeded")
        self.client.force_login(self.user)
        response = self.client.get(reverse("jobhunt_ai:run_status", args=[run.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(response, "Passer à Premium")

        self.assertContains(response, 'href="/upgrade/"')
        self.assertNotContains(response, 'hx-trigger="every')

    def test_synchronous_quota_exception_is_caught_by_view(self):
        request = RequestFactory().post("/", HTTP_HX_REQUEST="true")
        request.user = self.user
        request.resolver_match = None
        view = premium_required(Mock(side_effect=QuotaExceededException(
            tier="free", limit=2, period="month", reset_at=NOW,
        )))
        response = view(request)
        self.assertContains(response, "Passer à Premium")


@override_settings(IS_SAAS_PRODUCTION=True)
class QuotaFanoutTests(FanoutFixtures, TestCase):
    def test_quota_survives_aggregation_of_partial_results(self):
        self.set_up_fanout()
        first, second = self.dispatch()
        self.scrape(first)
        self.scrape(second)
        with patch("jobhunt_ai.agents.scout.analyze_page", side_effect=QuotaExceededException(
            tier="free", limit=10, period="month", reset_at=NOW,
        )):
            fanout.analyze_target(str(first.pk), self.owner.pk)
        self.analyze(second)
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.SUCCEEDED)
        self.assertTrue(self.agent_run.result["partial"])
        self.assertEqual(self.agent_run.result["quota"]["code"], "ai_quota_exceeded")
        self.client.force_login(self.owner)
        response = self.client.get(reverse("jobhunt_ai:run_status", args=[self.agent_run.pk]), {"poll": "1"})
        self.assertContains(response, "Les pistes déjà trouvées")
        self.assertNotIn("HX-Refresh", response.headers)


@override_settings(IS_SAAS_PRODUCTION=True, JOBHUNT_AI_FREE_MONTHLY_REQUESTS=2, JOBHUNT_AI_FREE_REQUESTS_PER_MINUTE=2)
class ConcurrentQuotaTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_first_requests_from_concurrent_workers_cannot_overspend(self):
        user = make_user("Concurrent quota user")
        start = Barrier(6)

        def attempt(_):
            try:
                start.wait(timeout=10)
                with app_role():
                    DjangoAIQuotaStore().consume(user.pk)
                return True
            except QuotaExceededException:
                return False
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=6) as pool:
            accepted = list(pool.map(attempt, range(6)))
        self.assertEqual(sum(accepted), 2)
        self.assertEqual(UserAIQuota.objects.get(owner=user).requests, 2)
