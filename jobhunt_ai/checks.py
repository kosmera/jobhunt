"""Refuse configurations that break durable submission or timeout recovery."""

from django.apps import apps
from django.conf import settings
from django.core.checks import Error, register

from jobhunt_ai import conf


@register("jobhunt_ai")
def check_queue(app_configs, **kwargs):
    config = getattr(settings, "Q_CLUSTER", {})
    problems = []
    if not isinstance(config, dict):
        return [Error("Q_CLUSTER must be a dictionary.", id="jobhunt_ai.E001")]
    if not apps.is_installed("django_q") or config.get("orm") != "default":
        problems.append(Error(
            "Install django_q and use Q_CLUSTER['orm']='default' for atomic submission.",
            id="jobhunt_ai.E001",
        ))
    if config.get("sync") or conf.EAGER_RUNS:
        problems.append(Error(
            "Disable Q_CLUSTER['sync'] and JOBHUNT_AI_EAGER in web and worker processes.",
            id="jobhunt_ai.E002",
        ))
    timeout = config.get("timeout", 0)
    workers = config.get("workers", 0)
    retry = config.get("retry", 0)
    if (
        type(retry) is not int or type(timeout) is not int or timeout <= 0
        or not isinstance(workers, int) or workers <= 0
        or config.get("queue_limit") != workers or config.get("bulk") != 1
        or retry < 3 * timeout + 2 * conf.WORKER_GRACE
    ):
        problems.append(Error(
            "Use positive timeout/workers, queue_limit=workers, bulk=1, "
            "and retry >= 3 * timeout + 120 (includes prefetched work).",
            id="jobhunt_ai.E003",
        ))
    if conf.QUEUE_TTL <= 0 or conf.LLM_TIMEOUT <= 0 or conf.LLM_MAX_RETRIES < 0:
        problems.append(Error("Invalid AI timeout/retry settings.", id="jobhunt_ai.E004"))
    if (
        not isinstance(workers, int) or workers < 2
        or not isinstance(timeout, int)
        or not 0 < conf.SCOUT_SCRAPE_TIMEOUT <= timeout
        or not 0 < conf.SCOUT_ANALYZE_TIMEOUT <= timeout
    ):
        problems.append(Error(
            "Fan-out requires at least two workers and positive stage timeouts <= Q_CLUSTER timeout.",
            id="jobhunt_ai.E005",
        ))
    return problems
