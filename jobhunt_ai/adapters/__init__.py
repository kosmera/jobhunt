"""Select a provider independently from copilot access and user allowances."""

from django.core.exceptions import ImproperlyConfigured
from jobhunt_ai.ports import AIPort
from jobhunt_ai.settings import get, is_saas_production


def get_ai_port() -> AIPort:
    production = is_saas_production()
    # Keep legacy installations compatible; deployed environments select explicitly.
    provider = get("PROVIDER") or ("azure_openai" if production else "anthropic")
    if provider == "openai":
        from jobhunt_ai.adapters.openai import OpenAIAdapter

        adapter = OpenAIAdapter()
    elif provider == "azure_openai":
        from jobhunt_ai.adapters.azure_openai import AzureOpenAIAdapter

        adapter = AzureOpenAIAdapter()
    elif provider == "anthropic":
        from jobhunt_ai.adapters.anthropic import AnthropicAdapter
        from jobhunt_ai.services.llm import get_client

        adapter = AnthropicAdapter(client_factory=get_client)
    else:
        raise ImproperlyConfigured("Unsupported JOBHUNT_AI_PROVIDER.")

    if not production:
        return adapter

    from jobhunt_ai.adapters.quota_store import DjangoAIQuotaStore
    from jobhunt_ai.quotas import ProductionQuotaProxy

    return ProductionQuotaProxy(
        adapter, DjangoAIQuotaStore(), enabled=is_saas_production
    )
