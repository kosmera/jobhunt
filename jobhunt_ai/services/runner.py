"""Durable Django-Q2 execution, with short, owner-scoped database blocks.

The ORM message and AgentRun share a transaction on ``default``. A worker
cannot see either until commit; rollback removes both. Do not switch this
publisher to an external broker without introducing a transactional outbox.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import rls
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db import close_old_connections, connection, transaction
from django.db.models import Q
from django.utils import timezone
from django_q.exceptions import TimeoutException

from jobhunt_ai import conf
from jobhunt_ai.quotas import QuotaExceededException
from jobhunt_ai.access import require_run_access
from jobhunt_ai.models import AgentRun, CandidateProfile, RunKind, RunStatus
from tracker.models import Application

logger = logging.getLogger(__name__)
ACTIVE = (RunStatus.PENDING, RunStatus.RUNNING)


def sweep_orphans(user, *, run_id=None) -> int:
    """Expire deadlines, never infer worker ownership from web process age.

    Also handles pre-Q2 rows without a deadline after QUEUE_TTL. The management
    command runs this without a browser; polling is an additional recovery path.
    """
    owner_id = getattr(user, "pk", user)
    if owner_id is None:
        return 0
    from jobhunt_ai.services.fanout import reconcile_targets

    expired = reconcile_targets(owner_id, run_id=run_id)
    now = timezone.now()
    with rls.as_user(owner_id):
        runs = AgentRun.objects.filter(
            owner_id=owner_id, status__in=ACTIVE, fanout_started_at__isnull=True,
        )
        if run_id is not None:
            runs = runs.filter(pk=run_id)
        return expired + runs.filter(
            Q(deadline_at__lte=now)
            | Q(deadline_at__isnull=True, created_at__lte=now - timedelta(seconds=conf.QUEUE_TTL))
        ).update(
            status=RunStatus.FAILED,
            error="Délai dépassé ou worker interrompu. Tu peux relancer l'opération.",
            finished_at=now,
        )


def progress(run_id, owner_id, phase: str, current=0, total=None) -> None:
    if run_id is None:
        return
    with rls.as_user(owner_id):
        AgentRun.objects.filter(
            pk=run_id, owner_id=owner_id, status=RunStatus.RUNNING
        ).update(phase=phase, progress_current=current, progress_total=total)


def _dispatch(run: AgentRun) -> dict | None:
    from jobhunt_ai.agents import cv_parser, generator, matcher, scout

    handlers = {
        RunKind.PARSE_CV: cv_parser.run,
        RunKind.MATCH: matcher.run,
        RunKind.GENERATE_CV: generator.run,
        RunKind.SCOUT: scout.run,
    }
    handler = handlers.get(run.kind)
    if handler is None:
        raise ValueError(f"Type d'exécution inconnu : {run.kind}")
    return handler(run)


def execute(run_id: int, owner_id: int, *, raise_errors=False) -> None:
    """Claim once with a conditional UPDATE; duplicates never call the LLM.

    Ordinary failures become FAILED. A hard-killed worker cannot run finally:
    its receipt is redelivered by the ORM broker and its expired deadline is
    reconciled here (and by the independent maintenance command).
    """
    own_connection = not connection.in_atomic_block
    if own_connection:
        close_old_connections()
    try:
        sweep_orphans(owner_id, run_id=run_id)
        with rls.as_user(owner_id), transaction.atomic():
            now = timezone.now()
            claimed = AgentRun.objects.filter(
                pk=run_id, owner_id=owner_id, status=RunStatus.PENDING
            ).update(
                status=RunStatus.RUNNING,
                started_at=now,
                deadline_at=now + timedelta(
                    seconds=settings.Q_CLUSTER["timeout"] + conf.WORKER_GRACE
                ),
                error="",
                phase="Démarrage",
            )
            if not claimed:
                return
            run = AgentRun.objects.get(pk=run_id, owner_id=owner_id)
        try:
            with rls.as_user(owner_id):
                require_run_access(get_user_model().objects.get(pk=owner_id), run.kind)
            # Scraping and LLM calls run outside database transactions.
            result = _dispatch(run)
        except QuotaExceededException as exc:
            with rls.as_user(owner_id):
                run.mark_failed(str(exc), quota=exc.as_dict())
        except TimeoutException:
            # Q2's cooperative timeout inherits SystemExit, not Exception.
            # Preserve it so Q2 recycles the worker after recording failure.
            with rls.as_user(owner_id):
                run.mark_failed("Le traitement a dépassé sa durée maximale. Tu peux le relancer.")
            raise
        except Exception as exc:
            logger.exception("Échec de l'exécution %s (%s)", run.pk, run.kind)
            # Domain/LLM errors already provide user-facing explanations.
            # Unexpected errors must not expose SQL, credentials or traces.
            message = (
                str(exc)[:1000] if isinstance(exc, (RuntimeError, ValueError, PermissionDenied))
                else "Erreur interne pendant le traitement. Réessaie ou contacte le support."
            )
            with rls.as_user(owner_id):
                run.mark_failed(message or "Le traitement a échoué.")
            if raise_errors:
                # Q2 stores its error globally: only an opaque run reference.
                raise RuntimeError(f"AgentRun {run_id} failed; see application logs.") from None
        else:
            # Fan-out hands completion to its children; dispatch is not success.
            if result is not None:
                with rls.as_user(owner_id):
                    run.mark_succeeded(result)
    finally:
        if own_connection:
            close_old_connections()


def execute_queued(run_id: int, owner_id: int) -> None:
    """Importable Q2 entry point. Payload and queue results contain no CV text."""
    execute(run_id, owner_id, raise_errors=True)


def launch(
    kind: str,
    *,
    owner=None,
    params: dict | None = None,
    application=None,
    profile=None,
) -> AgentRun:
    if owner is None:
        owner = getattr(profile, "owner", None)
        if owner is None and application is not None:
            owner = get_user_model().objects.get(pk=application.owner_id)
    if owner is None or owner.pk is None:
        raise ValueError("Une exécution appartient à un compte : précise owner=.")
    if profile is not None and profile.owner_id != owner.pk:
        raise ValueError("Le profil doit appartenir au compte.")
    if application is not None and not Application.objects.filter(
        owner_id=owner.pk, pk=application.pk
    ).exists():
        raise ValueError("La candidature doit appartenir au compte.")
    if kind not in RunKind.values:
        raise ValueError("Type d'exécution inconnu.")
    if not conf.EAGER_RUNS:
        from django_q.conf import Conf
        if Conf.ORM != "default" or Conf.SYNC:
            raise ImproperlyConfigured("JobHunt AI requires Q2 orm='default', sync=False.")

    with rls.as_user(owner.pk), transaction.atomic():
        # Serialize submissions for this account across web processes. SQLite
        # uses the host's IMMEDIATE transactions; production uses PostgreSQL.
        owner = get_user_model().objects.select_for_update().get(pk=owner.pk)
        require_run_access(owner, kind)
        sweep_orphans(owner)
        active = AgentRun.objects.filter(owner=owner, status__in=ACTIVE)
        if application is not None:
            existing = active.filter(application=application).first()
        elif kind == RunKind.SCOUT:
            existing = active.filter(kind=kind).first()
        else:
            existing = None  # Multiple distinct CV uploads may be queued.
        if existing is not None:
            return existing
        if profile is None and kind != RunKind.PARSE_CV:
            profile = CandidateProfile.primary(owner)
        run = AgentRun.objects.create(
            owner=owner, kind=kind, params=params or {}, application=application,
            profile=profile, model_id=conf.MODEL_ID,
            deadline_at=timezone.now() + timedelta(seconds=conf.QUEUE_TTL),
        )
        if not conf.EAGER_RUNS:
            from django_q.tasks import async_task

            run.task_id = async_task(
                "jobhunt_ai.services.runner.execute_queued", run.pk, run.owner_id,
                q_options={"sync": False, "ack_failure": True},
            )
            run.save(update_fields=["task_id"])
    if conf.EAGER_RUNS:
        # Explicit test seam only. Never enable in deployed HTTP processes.
        execute(run.pk, run.owner_id)
        run.refresh_from_db()
    return run
