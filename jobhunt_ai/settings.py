"""Lazy, namespaced configuration for the copilot.

Values come from Django settings (``JOBHUNT_AI_*``), never from the process
environment: ``jobhunt/settings.py`` decides how to load secrets. Reads are
not cached, so ``override_settings`` and deployment configuration stay
coherent.
"""

from copy import deepcopy
from typing import Any

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured


DEFAULTS = {
    "PROVIDER": "",
    "OPENAI_API_KEY": "",
    "OPENAI_SIMPLE_MODEL": "gpt-4o-mini-2024-07-18",
    "OPENAI_COMPLEX_MODEL": "gpt-4o-2024-08-06",
    "OPENAI_MAX_INPUT_CHARS": 100000,
    "OPENAI_MAX_OUTPUT_TOKENS": 8192,
    "API_KEY": "",
    "AZURE_ENDPOINT": "",
    "AZURE_API_KEY": "",
    "AZURE_API_VERSION": "2024-10-21",
    "AZURE_SIMPLE_DEPLOYMENT": "gpt-4o-mini",
    "AZURE_COMPLEX_DEPLOYMENT": "gpt-4o",
    "AZURE_MAX_INPUT_CHARS": 100000,
    "AZURE_MAX_OUTPUT_TOKENS": 8192,
    "FREE_MONTHLY_REQUESTS": 10,
    "FREE_REQUESTS_PER_MINUTE": 3,
    "PAID_MONTHLY_REQUESTS": 500,
    "PAID_REQUESTS_PER_MINUTE": 20,
    "UPGRADE_URL": "",
    "MODEL": "claude-opus-5",
    "MAX_TOKENS": 16000,
    "EAGER": False,
    "QUEUE_TTL": 86400,
    "WORKER_GRACE": 60,
    "LLM_TIMEOUT": 120.0,
    "LLM_MAX_RETRIES": 2,
    "SCRAPE_TIMEOUT": 180,
    "ANALYZE_TIMEOUT": 900,
    "LOCATION": "Nivelles, Belgique",
    "RADIUS_KM": 40,
    "BRIGHTDATA_API_TOKEN": "",
    "BRIGHTDATA_MCP_URL": "https://mcp.brightdata.com/mcp",
    "BRIGHTDATA_TIMEOUT": 90.0,
    "BRIGHTDATA_FALLBACK": True,
    "SCOUT_MAX_QUERIES": 2,
    "SCOUT_MAX_PAGES": 8,
    "SCOUT_PAGE_CHARS": 28000,
    "SCOUT_SOURCES": [
        {
            "name": "ICTjob",
            "url": "https://www.ictjob.be/fr/chercher-emplois-it?keywords={query}",
        },
        {"name": "Jobat", "url": "https://www.jobat.be/fr/emplois?keyword={query}"},
        {
            "name": "LinkedIn",
            "url": "https://www.linkedin.com/jobs/search?keywords={query}&location={location}&f_TPR=r604800",
        },
        {
            "name": "Indeed",
            "url": "https://be.indeed.com/emplois?q={query}&l={location}&radius={radius}&fromage=7",
        },
    ],
}


def get(name: str) -> Any:
    """Read a dynamic Django setting; startup validation checks its value.

    Options have different types, so this boundary intentionally returns Any.
    The compatibility proxy declares the types of its public settings.
    """
    if name not in DEFAULTS:
        raise AttributeError(name)
    return getattr(django_settings, f"JOBHUNT_AI_{name}", deepcopy(DEFAULTS[name]))


def is_saas_production() -> bool:
    value = getattr(django_settings, "IS_SAAS_PRODUCTION", False)
    if type(value) is not bool:
        raise ImproperlyConfigured("IS_SAAS_PRODUCTION must be a boolean.")
    return value


def __getattr__(name: str) -> Any:
    return get(name)
