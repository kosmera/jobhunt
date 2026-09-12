"""Premium gates cover web, signals, durable submission and later workers."""

from datetime import timedelta
from unittest.mock import Mock, patch

import anthropic
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_q.models import OrmQ

from accounts.models import Profile
from accounts.testing import make_user
from jobhunt_ai.access import has_copilot_access
from jobhunt_ai.models import AgentRun, CandidateProfile, OfferLead, RunKind, RunStatus
from jobhunt_ai.services import llm, runner
from jobhunt_ai.tests.fakes import PremiumTestCase, REMOTE_STORAGES
from jobhunt_ai.tests.test_agents import ingest_cv_document, make_application


@override_settings(STORAGES=REMOTE_STORAGES)
class PremiumAccessTests(PremiumTestCase):
    def revoke(self):
        Profile.objects.filter(user=self.user).update(premium_until=None)

    def test_payment_enables_copilot_in_the_same_session_without_a_key(self):
        self.revoke()
        with override_settings(DEBUG=False, JOBHUNT_AI_API_KEY=""):
            self.assertEqual(self.client.get(reverse("jobhunt_ai:copilot")).status_code, 402)
            Profile.objects.filter(user=self.user).update(
                premium_until=timezone.now() + timedelta(days=30)
            )
            self.assertEqual(self.client.get(reverse("jobhunt_ai:copilot")).status_code, 200)
            Profile.objects.filter(user=self.user).update(premium_until=timezone.now())
            self.assertEqual(self.client.get(reverse("jobhunt_ai:copilot")).status_code, 402)

    def test_local_administrator_can_manage_core_but_has_no_copilot_access(self):
        self.revoke()
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_staff)
        self.assertTrue(self.user.is_superuser)
        self.assertFalse(has_copilot_access(self.user))
        self.assertEqual(self.client.get(reverse("jobhunt_ai:copilot")).status_code, 402)
        with self.assertRaises(PermissionDenied):
            runner.launch(RunKind.SCOUT, owner=self.user)
        self.assertFalse(OrmQ.objects.exists())

    def test_free_account_cannot_submit_or_read_existing_ai_data(self):
        application = make_application(owner=self.user)
        run = AgentRun.objects.create(owner=self.user, kind=RunKind.SCOUT)
        self.revoke()
        for route, args in (
            ("copilot", []), ("profile", []), ("leads", []),
            ("run_status", [run.pk]), ("evaluate", [application.pk]),
            ("parse_cv", []), ("generate_cv", [application.pk]), ("scout_start", []),
            ("api_scout_start", []), ("api_run_status", [run.pk]),
            ("api_run_leads", [run.pk]),
        ):
            with self.subTest(route=route):
                response = self.client.post(reverse(f"jobhunt_ai:{route}", args=args))
                self.assertEqual(response.status_code, 402)
        self.assertEqual(AgentRun.objects.count(), 1)
        self.assertFalse(OrmQ.objects.exists())

    def test_free_host_page_has_no_active_copilot_controls_or_badges(self):
        application = make_application(owner=self.user)
        OfferLead.objects.create(owner=self.user, title="Private lead")
        self.revoke()
        response = self.client.get(application.get_absolute_url())
        self.assertContains(response, "abonnement Premium actif")
        self.assertNotContains(response, 'hx-post="' + reverse("jobhunt_ai:evaluate", args=[application.pk]))
        self.assertNotIn("ai_leads", response.context["nav_counters"])

    def test_free_cv_is_stored_without_queuing_ai_then_paid_upload_queues(self):
        self.revoke()
        with patch.object(llm, "parse_structured") as provider:
            intake, run = ingest_cv_document(self.user)
            self.assertIsNotNone(intake.document.pk)
            self.assertIsNone(run)
            self.assertFalse(AgentRun.objects.exists())
            self.assertFalse(OrmQ.objects.exists())
            Profile.objects.filter(user=self.user).update(
                premium_until=timezone.now() + timedelta(days=30)
            )
            _, run = ingest_cv_document(self.user)
            self.assertIsNotNone(run)
            self.assertEqual(OrmQ.objects.count(), 1)
            provider.assert_not_called()

    def test_direct_submission_cannot_bypass_premium_with_related_objects(self):
        application = make_application(owner=self.user)
        profile = CandidateProfile.objects.create(owner=self.user, label="CV")
        self.revoke()
        for owner, related_application, related_profile in (
            (self.user, None, None), (None, application, None), (None, None, profile),
        ):
            with self.subTest(owner=owner), self.assertRaises(PermissionDenied):
                runner.launch(
                    RunKind.SCOUT, owner=owner,
                    application=related_application, profile=related_profile,
                )
        self.assertFalse(AgentRun.objects.exists())
        self.assertFalse(OrmQ.objects.exists())

    def test_queued_work_rechecks_entitlement_before_dispatch(self):
        run = runner.launch(RunKind.SCOUT, owner=self.user)
        self.revoke()
        with patch.object(runner, "_dispatch") as dispatch:
            runner.execute(run.pk, self.user.pk)
            dispatch.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn("Premium", run.error)

    def test_a_fresh_account_is_denied_and_the_profile_flag_follows_premium_until(self):
        user = make_user()
        self.assertFalse(has_copilot_access(user))
        profile = Profile.objects.get(user=user)
        self.assertFalse(profile.is_premium)
        profile.premium_until = timezone.now() + timedelta(days=30)
        self.assertTrue(profile.is_premium)
        self.assertFalse(has_copilot_access(user))  # DB-fresh: nothing saved yet
        profile.save(update_fields=["premium_until"])
        self.assertTrue(has_copilot_access(user))
        profile.premium_until = timezone.now() - timedelta(seconds=1)
        self.assertFalse(profile.is_premium)


class ProviderConfigurationTests(TestCase):
    @override_settings(JOBHUNT_AI_API_KEY="")
    def test_missing_provider_is_an_execution_error_without_customer_setup_instructions(self):
        with patch.object(llm, "_configured_client") as factory:
            with self.assertRaises(llm.LLMError) as caught:
                llm.get_client()
        factory.assert_not_called()
        self.assertNotIn("JOBHUNT_AI", str(caught.exception))
        self.assertIn("indisponible", str(caught.exception))

    def test_invalid_provider_credentials_do_not_ask_the_customer_for_a_key(self):
        from jobhunt_ai.agents.schemas import ParsedProfile

        error = anthropic.AuthenticationError(
            "invalid operator credential",
            response=Mock(status_code=401, request=Mock(), headers={}),
            body=None,
        )
        with patch.object(llm, "get_client") as client:
            client.return_value.messages.parse.side_effect = error
            with self.assertRaises(llm.LLMError) as caught:
                llm.parse_structured(ParsedProfile, system="test", content="test")
        self.assertEqual(str(caught.exception), llm.SERVICE_UNAVAILABLE)
