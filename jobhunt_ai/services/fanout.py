"""Independent scrape → analysis chains with durable, tenant-scoped results.

Django-Q2 Chain does not forward return values and may advance after failure.
Analysis therefore claims only its own committed SCRAPED target.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

import rls
from django.db import close_old_connections, connection, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from django_q.exceptions import TimeoutException
from django_q.tasks import Chain

from jobhunt_ai import conf
from jobhunt_ai.quotas import QuotaExceededException
from jobhunt_ai.models import (
    AgentRun,
    OfferLead,
    RunStatus,
    ScoutTarget,
    TargetStatus,
)
from jobhunt_ai.scraping.sources import ScrapeError

logger = logging.getLogger(__name__)
TERMINAL = (TargetStatus.SUCCEEDED, TargetStatus.FAILED)


def dispatch(
    run_id: int,
    owner_id: int,
    context: dict,
    sources: list[dict],
) -> None:
    """Commit the complete target manifest and independent ORM chains together."""
    unique = {
        hashlib.sha256(
            json.dumps(source, sort_keys=True).encode()
        ).hexdigest(): source
        for source in sources
    }
    if not unique:
        raise ValueError("Aucune source configurée.")

    with rls.as_user(owner_id), transaction.atomic():
        run = AgentRun.objects.select_for_update().get(
            pk=run_id,
            owner_id=owner_id,
        )
        if run.status != RunStatus.RUNNING or run.fanout_started_at is not None:
            return

        now = timezone.now()
        targets = ScoutTarget.objects.bulk_create([
            ScoutTarget(
                run=run,
                key=key,
                source=source,
                deadline_at=now + timedelta(seconds=conf.QUEUE_TTL),
            )
            for key, source in unique.items()
        ])

        run.fanout_started_at = now
        run.fanout_context = {
            **context,
            "run_id": run_id,
            "owner_id": owner_id,
        }
        run.deadline_at = None
        run.phase = "Recherche et analyse en parallèle"
        run.progress_current = 0
        run.progress_total = len(targets)
        run.save(update_fields=[
            "fanout_started_at",
            "fanout_context",
            "deadline_at",
            "phase",
            "progress_current",
            "progress_total",
        ])

        # Publish only: no network calls or waiting inside this transaction.
        for target in targets:
            chain = Chain(group=target.pk.hex, cached=False, sync=False)
            chain.append(
                "jobhunt_ai.services.fanout.scrape_target",
                str(target.pk),
                owner_id,
                stage_timeout=conf.SCOUT_SCRAPE_TIMEOUT,
                timeout=conf.SCOUT_SCRAPE_TIMEOUT,
                ack_failure=True,
                save=True,
            )
            chain.append(
                "jobhunt_ai.services.fanout.analyze_target",
                str(target.pk),
                owner_id,
                stage_timeout=conf.SCOUT_ANALYZE_TIMEOUT,
                timeout=conf.SCOUT_ANALYZE_TIMEOUT,
                ack_failure=True,
                save=True,
            )
            chain.run()


@contextmanager
def _locked_target(
    target_id: str,
    owner_id: int,
) -> Iterator[tuple[AgentRun | None, ScoutTarget | None]]:
    # All writers acquire the parent lock first, then read fresh target state.
    with rls.as_user(owner_id), transaction.atomic():
        run_id = ScoutTarget.objects.filter(
            pk=target_id,
            run__owner_id=owner_id,
        ).values_list("run_id", flat=True).first()

        run = AgentRun.objects.select_for_update().filter(
            pk=run_id,
            owner_id=owner_id,
        ).first()

        target = (
            ScoutTarget.objects.filter(pk=target_id, run=run).first()
            if run else None
        )
        yield run, target


def _aggregate(run: AgentRun) -> None:
    if run.status != RunStatus.RUNNING:
        return

    counts = run.targets.aggregate(
        total=Count("pk"),
        done=Count("pk", filter=Q(status__in=TERMINAL)),
        succeeded=Count("pk", filter=Q(status=TargetStatus.SUCCEEDED)),
        failed=Count("pk", filter=Q(status=TargetStatus.FAILED)),
        created=Sum("created_leads", default=0),
    )

    run.progress_current = counts["done"]
    run.progress_total = counts["total"]
    run.result = {
        **({"quota": run.result["quota"]} if "quota" in run.result else {}),
        "created": counts["created"],
        "queries": run.fanout_context.get("queries", []),
        "targets_total": counts["total"],
        "targets_succeeded": counts["succeeded"],
        "targets_failed": counts["failed"],
        "partial": bool(counts["failed"]),
        "warnings": [
            f"{name}: {error}"
            for name, error in run.targets.filter(
                status=TargetStatus.FAILED,
            ).order_by("created_at", "pk").values_list("source__name", "error")
        ],
    }

    if counts["done"] == counts["total"]:
        run.status = (
            RunStatus.SUCCEEDED if counts["succeeded"] else RunStatus.FAILED
        )
        run.finished_at = timezone.now()
        run.phase = "Terminée"
        run.error = (
            "" if counts["succeeded"]
            else "Aucune source n'a pu être traitée."
        )

    run.save(update_fields=[
        "progress_current",
        "progress_total",
        "result",
        "status",
        "finished_at",
        "phase",
        "error",
    ])


def _is_active(run: AgentRun, target: ScoutTarget, expected: TargetStatus) -> bool:
    if (
        run.status != RunStatus.RUNNING
        or target.status != expected
    ):
        return False

    if target.deadline_at <= timezone.now():
        target.status = TargetStatus.FAILED
        target.error = "Délai de traitement dépassé pour cette source."
        target.finished_at = timezone.now()
        target.save(update_fields=["status", "error", "finished_at"])
        _aggregate(run)
        return False

    return True


def _claim(
    target_id: str,
    owner_id: int,
    expected: TargetStatus,
    active: TargetStatus,
    timeout: int,
) -> tuple[ScoutTarget, dict] | None:
    with _locked_target(target_id, owner_id) as (run, target):
        if run is None or target is None:
            return None
        if not _is_active(run, target, expected):
            return None

        from jobhunt_ai.access import PREMIUM_REQUIRED, has_copilot_access

        if not has_copilot_access(run.owner):
            target.status = TargetStatus.FAILED
            target.error = PREMIUM_REQUIRED
            target.finished_at = timezone.now()
            target.save(update_fields=["status", "error", "finished_at"])
            _aggregate(run)
            return None

        now = timezone.now()
        target.status = active
        target.started_at = target.started_at or now
        target.deadline_at = now + timedelta(
            seconds=timeout + conf.WORKER_GRACE,
        )
        target.save(update_fields=["status", "started_at", "deadline_at"])
        return target, run.fanout_context


def _fail(
    target_id: str, owner_id: int, active: TargetStatus, message: str,
    *, quota: dict | None = None,
) -> None:
    with _locked_target(target_id, owner_id) as (run, target):
        if run is not None and target is not None and target.status == active:
            target.status = TargetStatus.FAILED
            target.error = message
            target.finished_at = timezone.now()
            target.save(update_fields=["status", "error", "finished_at"])
            if quota is not None:
                run.result = {**run.result, "quota": quota}
            _aggregate(run)


@contextmanager
def _stage(
    target_id: str,
    owner_id: int,
    expected: TargetStatus,
    active: TargetStatus,
    timeout: int,
) -> Iterator[tuple[ScoutTarget, dict] | None]:
    own_connection = not connection.in_atomic_block
    claimed = None

    if own_connection:
        close_old_connections()

    try:
        claimed = _claim(target_id, owner_id, expected, active, timeout)
        yield claimed
    except QuotaExceededException as exc:
        if claimed:
            _fail(target_id, owner_id, active, str(exc), quota=exc.as_dict())
    except TimeoutException:
        if claimed:
            _fail(
                target_id,
                owner_id,
                active,
                "Délai de traitement dépassé pour cette source.",
            )
        # TimeoutException inherits SystemExit; let Q2 recycle this worker.
        raise
    except Exception as exc:
        logger.exception("Scout target %s failed in %s", target_id, active)
        if claimed:
            _fail(
                target_id,
                owner_id,
                active,
                str(exc) if isinstance(exc, ScrapeError) else "Échec du traitement de cette source.",
            )
        # Keep provider errors and candidate text out of shared Q2 results.
        raise RuntimeError(
            f"ScoutTarget {target_id} failed in {active}."
        ) from None
    finally:
        if own_connection:
            close_old_connections()


def scrape_target(
    target_id: str,
    owner_id: int,
    *,
    stage_timeout: int = conf.SCOUT_SCRAPE_TIMEOUT,
) -> None:
    from jobhunt_ai.scraping.sources import fetch_page

    with _stage(
        target_id,
        owner_id,
        TargetStatus.PENDING,
        TargetStatus.SCRAPING,
        stage_timeout,
    ) as claimed:
        if claimed is None:
            return

        target, _ = claimed
        if target.source.get("configuration_error"):
            raise ValueError("Cette source est mal configurée.")

        # Network work occurs outside database transactions.
        text = fetch_page(target.source)
        if not text or not text.strip():
            raise ValueError("Page vide.")

        with _locked_target(target_id, owner_id) as (run, current):
            if run is None or current is None:
                return
            if _is_active(run, current, TargetStatus.SCRAPING):
                current.scraped_text = text[:conf.SCOUT_PAGE_CHAR_LIMIT]
                current.status = TargetStatus.SCRAPED
                current.deadline_at = timezone.now() + timedelta(
                    seconds=conf.QUEUE_TTL,
                )
                current.save(update_fields=[
                    "scraped_text",
                    "status",
                    "deadline_at",
                ])

    # Q2 now queues this target's analysis without waiting for other targets.


def analyze_target(
    target_id: str,
    owner_id: int,
    *,
    stage_timeout: int = conf.SCOUT_ANALYZE_TIMEOUT,
) -> None:
    from jobhunt_ai.agents.scout import analyze_page
    from jobhunt_ai.scraping.sources import deduplicate

    with _stage(
        target_id,
        owner_id,
        TargetStatus.SCRAPED,
        TargetStatus.ANALYZING,
        stage_timeout,
    ) as claimed:
        if claimed is None:
            return

        target, context = claimed

        # Extraction and CV matching occur outside database transactions.
        leads = analyze_page(target.source, target.scraped_text, context)

        with _locked_target(target_id, owner_id) as (run, current):
            if run is None or current is None:
                return
            if not _is_active(run, current, TargetStatus.ANALYZING):
                return

            # Parallel sources may discover the same offer. Recheck while
            # holding the parent lock, then commit outputs and state together.
            leads = deduplicate(leads, owner_id)
            OfferLead.objects.bulk_create([
                OfferLead(owner_id=owner_id, run=run, **lead)
                for lead in leads
            ], batch_size=100)

            current.status = TargetStatus.SUCCEEDED
            current.created_leads = len(leads)
            current.finished_at = timezone.now()
            current.scraped_text = ""
            current.save(update_fields=[
                "status",
                "created_leads",
                "finished_at",
                "scraped_text",
            ])
            _aggregate(run)


def reconcile_targets(owner_id: int, *, run_id: int | None = None) -> int:
    """Expire abandoned targets without cancelling unexpired siblings.

    Called by the independent reconciliation timer and status polling.
    Covers SIGKILL, OOM, worker outages and lost Chain continuations.
    """
    expired = 0

    with rls.as_user(owner_id):
        parents = AgentRun.objects.filter(
            owner_id=owner_id,
            status=RunStatus.RUNNING,
            fanout_started_at__isnull=False,
        )
        if run_id is not None:
            parents = parents.filter(pk=run_id)
        parent_ids = list(parents.values_list("pk", flat=True))

    for parent_id in parent_ids:
        with rls.as_user(owner_id), transaction.atomic():
            run = AgentRun.objects.select_for_update().filter(
                pk=parent_id,
                owner_id=owner_id,
                status=RunStatus.RUNNING,
            ).first()
            if run is None:
                continue

            now = timezone.now()
            expired += run.targets.exclude(
                status__in=TERMINAL,
            ).filter(
                deadline_at__lte=now,
            ).update(
                status=TargetStatus.FAILED,
                error="Délai dépassé ou worker interrompu.",
                finished_at=now,
            )
            _aggregate(run)

    return expired
