"""Startup validation without database queries or online credential checks."""

from math import isfinite

from django.core.exceptions import ImproperlyConfigured

from jobhunt_ai.settings import get, is_saas_production


def validate_settings() -> None:
    """Refuse to start when a ``JOBHUNT_AI_*`` option has an invalid value."""
    provider = get("PROVIDER") or ("azure_openai" if is_saas_production() else "anthropic")
    if provider not in ("openai", "azure_openai", "anthropic"):
        raise ImproperlyConfigured("JOBHUNT_AI_PROVIDER must be openai, azure_openai or anthropic.")
    if provider in ("openai", "azure_openai"):
        prefix = "OPENAI" if provider == "openai" else "AZURE"
        for name in (f"{prefix}_MAX_INPUT_CHARS", f"{prefix}_MAX_OUTPUT_TOKENS"):
            if type(get(name)) is not int or get(name) <= 0:
                raise ImproperlyConfigured(f"JOBHUNT_AI_{name} must be a positive integer.")
        names = ("OPENAI_SIMPLE_MODEL", "OPENAI_COMPLEX_MODEL") if provider == "openai" else (
            "AZURE_SIMPLE_DEPLOYMENT", "AZURE_COMPLEX_DEPLOYMENT"
        )
        for name in names:
            if not isinstance(get(name), str) or not get(name).strip():
                raise ImproperlyConfigured(f"JOBHUNT_AI_{name} must not be empty.")
    if is_saas_production():
        from jobhunt_ai.quotas import QuotaLimits

        for tier in ("FREE", "PAID"):
            try:
                QuotaLimits(get(f"{tier}_MONTHLY_REQUESTS"), get(f"{tier}_REQUESTS_PER_MINUTE"))
            except ValueError as exc:
                raise ImproperlyConfigured(str(exc)) from exc
    model = get("MODEL")
    if not isinstance(model, str) or not model.strip():
        raise ImproperlyConfigured("Set JOBHUNT_AI_MODEL in the Django settings.")
    for name in ("EAGER", "BRIGHTDATA_FALLBACK"):
        if not isinstance(get(name), bool):
            raise ImproperlyConfigured(f"JOBHUNT_AI_{name} must be a boolean.")
    integer_options = (
        "MAX_TOKENS",
        "QUEUE_TTL",
        "WORKER_GRACE",
        "SCRAPE_TIMEOUT",
        "ANALYZE_TIMEOUT",
        "RADIUS_KM",
        "SCOUT_MAX_QUERIES",
        "SCOUT_MAX_PAGES",
        "SCOUT_PAGE_CHARS",
    )
    for name in (*integer_options, "LLM_TIMEOUT", "BRIGHTDATA_TIMEOUT"):
        value = get(name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or value <= 0
            or (name in integer_options and type(value) is not int)
        ):
            raise ImproperlyConfigured(f"JOBHUNT_AI_{name} must be a positive number.")
    retries = get("LLM_MAX_RETRIES")
    if type(retries) is not int or retries < 0:
        raise ImproperlyConfigured(
            "JOBHUNT_AI_LLM_MAX_RETRIES must be a nonnegative integer."
        )
    sources = get("SCOUT_SOURCES")
    if not isinstance(sources, (list, tuple)) or any(
        not isinstance(source, dict)
        or not source.get("name")
        or not (source.get("url") or source.get("search"))
        for source in sources
    ):
        raise ImproperlyConfigured(
            "JOBHUNT_AI_SCOUT_SOURCES must be a list of source dictionaries."
        )
    from jobhunt_ai.checks import check_queue

    problems = check_queue(None)
    if problems:
        raise ImproperlyConfigured(
            "Invalid JobHunt AI queue configuration: "
            + " ".join(problem.msg for problem in problems)
        )
