"""Compatibility names for callers of the former environment-only settings.

New integrations configure only JOBHUNT_AI_* keys in Django settings.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from jobhunt_ai import settings

if TYPE_CHECKING:
    # Static declarations only: runtime reads must still honor override_settings.
    API_KEY: str
    MODEL_ID: str
    MAX_TOKENS: int
    EAGER_RUNS: bool
    QUEUE_TTL: int
    WORKER_GRACE: int
    LLM_TIMEOUT: float
    LLM_MAX_RETRIES: int
    SCOUT_SCRAPE_TIMEOUT: int
    SCOUT_ANALYZE_TIMEOUT: int
    DEFAULT_LOCATION: str
    DEFAULT_RADIUS_KM: int
    BRIGHTDATA_API_TOKEN: str
    BRIGHTDATA_MCP_URL: str
    BRIGHTDATA_TIMEOUT: float
    BRIGHTDATA_FALLBACK: bool
    SCOUT_MAX_QUERIES: int
    SCOUT_MAX_PAGES: int
    SCOUT_PAGE_CHAR_LIMIT: int

_ALIASES = {
    "MODEL_ID": "MODEL",
    "EAGER_RUNS": "EAGER",
    "SCOUT_SCRAPE_TIMEOUT": "SCRAPE_TIMEOUT",
    "SCOUT_ANALYZE_TIMEOUT": "ANALYZE_TIMEOUT",
    "DEFAULT_LOCATION": "LOCATION",
    "DEFAULT_RADIUS_KM": "RADIUS_KM",
    "SCOUT_PAGE_CHAR_LIMIT": "SCOUT_PAGE_CHARS",
}


def __getattr__(name: str) -> Any:
    return settings.get(_ALIASES.get(name, name))


def scout_sources() -> Sequence[dict[str, str]]:
    return settings.get("SCOUT_SOURCES")
