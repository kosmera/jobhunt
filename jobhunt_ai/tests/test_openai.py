"""Direct OpenAI transport, secret boundaries and provider/access independence."""

import builtins
import json
from unittest.mock import Mock, patch

import httpx
import openai
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from pydantic import BaseModel

from jobhunt_ai.adapters import get_ai_port
from jobhunt_ai.adapters.openai import ModelRouter, OpenAIAdapter, _configured_client
from jobhunt_ai.ports import LLMError, StructuredResult
from jobhunt_ai.quotas import ProductionQuotaProxy
from jobhunt_ai.validation import validate_settings


class Answer(BaseModel):
    text: str


@override_settings(JOBHUNT_AI_PROVIDER="openai", IS_SAAS_PRODUCTION=False)
class DirectOpenAITests(SimpleTestCase):
    def test_real_sdk_sends_direct_request_and_validates_structured_output(self):
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

        with openai.OpenAI(
            api_key="test-key", base_url="https://api.openai.com/v1", max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        ) as client:
            result = OpenAIAdapter(client=client, router=ModelRouter("mini", "deep")).parse_structured(
                Answer, system="test", content="test", max_tokens=50,
            )
        self.assertEqual(result, StructuredResult(Answer(text="valid"), 12, 5, "deep"))
        self.assertEqual(str(requests[0].url), "https://api.openai.com/v1/chat/completions")
        self.assertEqual(requests[0].headers["authorization"], "Bearer test-key")
        self.assertNotIn("api-key", requests[0].headers)
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["model"], "deep")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertEqual(payload["max_completion_tokens"], 50)
        self.assertFalse(payload["store"])

    def test_missing_or_unresolved_secret_never_creates_a_client(self):
        for key in ("", "@Microsoft.KeyVault(SecretUri=https://test.vault.azure.net/secrets/openai-api-key)"):
            with override_settings(JOBHUNT_AI_OPENAI_API_KEY=key), patch("openai.OpenAI") as sdk:
                with self.assertRaises(LLMError):
                    get_ai_port().parse_structured(Answer, system="s", content="c")
                sdk.assert_not_called()

    def test_direct_selection_needs_neither_azure_imports_nor_quota_database(self):
        original_import = builtins.__import__

        def without_azure(name, *args, **kwargs):
            if name == "azure" or name.startswith("azure."):
                raise AssertionError("Direct OpenAI must not load Azure SDKs")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=without_azure):
            self.assertIsInstance(get_ai_port(), OpenAIAdapter)

    @override_settings(IS_SAAS_PRODUCTION=True)
    def test_saas_quota_wrapper_retains_explicit_direct_provider(self):
        port = get_ai_port()
        self.assertIsInstance(port, ProductionQuotaProxy)
        assert isinstance(port, ProductionQuotaProxy)
        self.assertIs(type(port.wrapped), OpenAIAdapter)
        with override_settings(IS_SAAS_PRODUCTION=False):
            self.assertIs(type(get_ai_port()), OpenAIAdapter)

    def test_client_uses_known_endpoint_and_no_automatic_retries(self):
        _configured_client.cache_clear()
        self.addCleanup(_configured_client.cache_clear)
        with patch("openai.OpenAI") as sdk:
            _configured_client("test-key", 10)
        self.assertEqual(sdk.call_args.kwargs, {
            "api_key": "test-key", "base_url": "https://api.openai.com/v1",
            "timeout": 10, "max_retries": 0,
        })

    def test_provider_errors_do_not_leak_key_or_fall_back_to_azure(self):
        client = Mock()
        client.chat.completions.parse.side_effect = openai.AuthenticationError(
            "secret-key", response=httpx.Response(401, request=httpx.Request("POST", "https://api.openai.com")), body=None,
        )
        with self.assertRaises(LLMError) as caught:
            OpenAIAdapter(client=client).parse_structured(Answer, system="s", content="c")
        self.assertNotIn("secret-key", str(caught.exception))
        self.assertEqual(client.chat.completions.parse.call_count, 1)

    @override_settings(JOBHUNT_AI_OPENAI_MAX_INPUT_CHARS=5)
    def test_large_and_binary_input_do_not_reach_provider(self):
        client = Mock()
        for content in ("too long", [{"type": "document", "source": "raw pdf"}]):
            with self.assertRaises(LLMError):
                OpenAIAdapter(client=client).parse_structured(Answer, system="s", content=content)
        client.chat.completions.parse.assert_not_called()

    @override_settings(JOBHUNT_AI_PROVIDER="typo")
    def test_invalid_provider_fails_instead_of_falling_back(self):
        with self.assertRaises(ImproperlyConfigured):
            get_ai_port()
        with self.assertRaises(ImproperlyConfigured):
            validate_settings()
