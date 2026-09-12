"""Azure OpenAI client configuration over the shared structured-output adapter."""

from functools import lru_cache
import logging

import openai
from azure.core.exceptions import AzureError

from jobhunt_ai.adapters.openai import OpenAIAdapter, ModelRouter as ModelRouter
from jobhunt_ai.ports import LLMError, SERVICE_UNAVAILABLE
from jobhunt_ai.settings import get

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _configured_client(endpoint, api_key, api_version, timeout):
    options = {"api_key": api_key}
    if not api_key:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        options["azure_ad_token_provider"] = get_bearer_token_provider(
            DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
        )
    return openai.AzureOpenAI(
        azure_endpoint=endpoint, api_version=api_version,
        timeout=timeout, max_retries=0, **options,
    )


class AzureOpenAIAdapter(OpenAIAdapter):
    model_settings = ("AZURE_SIMPLE_DEPLOYMENT", "AZURE_COMPLEX_DEPLOYMENT")
    limit_prefix = "AZURE"
    sdk_errors = (openai.OpenAIError, AzureError)
    store_completion: bool | openai.Omit = openai.omit

    def _client(self):
        endpoint = get("AZURE_ENDPOINT")
        if not endpoint:
            logger.error("Configure the server's JOBHUNT_AI_AZURE_ENDPOINT setting.")
            raise LLMError(SERVICE_UNAVAILABLE)
        return _configured_client(
            endpoint, get("AZURE_API_KEY"), get("AZURE_API_VERSION"), get("LLM_TIMEOUT"),
        )
