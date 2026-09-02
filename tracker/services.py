"""Use cases: what the interface can do to an application, in one place.

Each function takes the entities it works on, the thresholds it needs as
plain ints (``follow_up_days``, ``stale_days`` — the caller reads the owner's
preferences, never this module), an optional ``today`` and the
``persistence`` to write through (the configured adapter by default). The
rules themselves live in ``tracker.domain``; this module sequences them
with the ports and draws the transaction boundaries.

Nothing here knows about requests, templates or the ORM, so every use case
runs unchanged on ``MemoryPersistence`` in a database-free test.
"""

from __future__ import annotations

import datetime as dt

from django.utils import timezone

from tracker import domain
from tracker.adapters import persistence as configured_persistence
from tracker.models import (
    IN_FLIGHT_STATUSES,
    INTERVIEWING_STATUSES,
    OPEN_STATUSES,
    PIPELINE_STATUSES,
    STATUS_TONE,
    ActivityEvent,
    Application,
    Contact,
    Document,
    EventKind,
    GapStatus,
    SkillGap,
    Status,
)
from tracker.ports import Persistence


def _store(persistence: Persistence | None) -> Persistence:
    return configured_persistence() if persistence is None else persistence


def _today(today: dt.date | None) -> dt.date:
    return today or timezone.localdate()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def change_status(
    application: Application,
    new_status: str,
    *,
    note: str = "",
    follow_up_days: int,
    persistence: Persistence | None = None,
    today: dt.date | None = None,
) -> bool:
    """Move to ``new_status``, keeping dates and the timeline in step.

    Returns ``True`` when something actually changed. On the ORM adapter the
    row and its timeline entry are written in one transaction, so a failure
    on either leaves the database as it was (the instance in memory keeps
    the planned values).
    """
    store = _store(persistence)
    transition = domain.plan_transition(
        application, new_status, today=_today(today), follow_up_days=follow_up_days
    )
    if transition is None:
        return False
    with store.atomic():
        store.applications.save(application, fields=transition.fields)
        store.events.add(
            application, transition.event_kind, transition.event_title, note, _today(today)
        )
    return True


def advance(
    application: Application,
    *,
    follow_up_days: int,
    persistence: Persistence | None = None,
    today: dt.date | None = None,
) -> str | None:
    """One step down the pipeline; ``None`` when there is no next step."""
    nxt = application.next_status
    if nxt is None:
        return None
    change_status(
        application, nxt, follow_up_days=follow_up_days, persistence=persistence, today=today
    )
    return nxt


def schedule_follow_up(
    application: Application, on: dt.date | None, *, persistence: Persistence | None = None
) -> None:
    """Set (or clear, with ``None``) the reminder date."""
    application.follow_up_on = on
    _store(persistence).applications.save(application, fields=["follow_up_on"])


def mark_followed_up(
    application: Application,
    *,
    follow_up_days: int,
    persistence: Persistence | None = None,
    today: dt.date | None = None,
) -> dt.date:
    """Log a follow-up as done and push the next reminder out; returns it."""
    store = _store(persistence)
    today = _today(today)
    application.follow_up_on = today + dt.timedelta(days=follow_up_days)
    with store.atomic():
        store.events.add(application, EventKind.FOLLOW_UP, "Relance envoyée", "", today)
        store.applications.save(application, fields=["follow_up_on"])
    return application.follow_up_on


def record_application(
    application: Application,
    title: str = "Candidature créée",
    *,
    persistence: Persistence | None = None,
) -> ActivityEvent:
    """The first timeline entry of a freshly saved application."""
    return _store(persistence).events.add(application, EventKind.NOTE, title)


# ---------------------------------------------------------------------------
# Satellites: events, documents, contacts, skill gaps
# ---------------------------------------------------------------------------


def add_event(
    application: Application, event: ActivityEvent, *, persistence: Persistence | None = None
) -> ActivityEvent:
    """Record an entry the user typed (an unsaved ``ActivityEvent``)."""
    return _store(persistence).events.add(
        application, event.kind, event.title, event.detail, event.happened_on
    )


def delete_event(owner, pk: int, *, persistence: Persistence | None = None) -> Application:
    """Remove one entry; returns its application so the panel can re-render.
    ``NotFound`` propagates when the entry is not the account's."""
    store = _store(persistence)
    event = store.events.get(owner, pk)
    application = event.application
    store.events.remove(event)
    return application


def attach_document(
    owner,
    application: Application | None,
    document: Document,
    *,
    label: str,
    persistence: Persistence | None = None,
) -> Document:
    """File a document under an application, or in the library (``None``).

    A new primary document of a kind demotes the previous one; an attached
    document leaves a trace on the timeline.
    """
    store = _store(persistence)
    document.owner = owner
    document.application = application
    document.label = label
    with store.atomic():
        if document.is_primary and application is not None:
            store.documents.demote_primary(application, document.kind)
        store.documents.add(document)
        if application is not None:
            store.events.add(application, EventKind.NOTE, f"Document ajouté : {document.label}")
    return document


def delete_document(owner, pk: int, *, persistence: Persistence | None = None) -> Document:
    """Remove a document; returns the removed instance (its ``application``,
    ``None`` for a library document, and ``label`` feed the response)."""
    store = _store(persistence)
    document = store.documents.get(owner, pk)
    store.documents.remove(document)
    return document


def add_contact(
    application: Application, contact: Contact, *, persistence: Persistence | None = None
) -> Contact:
    contact.application = application
    return _store(persistence).contacts.add(contact)


def delete_contact(owner, pk: int, *, persistence: Persistence | None = None) -> Application:
    store = _store(persistence)
    contact = store.contacts.get(owner, pk)
    application = contact.application
    store.contacts.remove(contact)
    return application


def delete_application(owner, pk: int, *, persistence: Persistence | None = None) -> Application:
    """Remove an application with everything hanging off it; returns the
    removed instance so the confirmation can name it."""
    store = _store(persistence)
    application = store.applications.get(owner, pk)
    store.applications.remove(application)
    return application


def set_gap_status(
    owner, pk: int, status: str, *, persistence: Persistence | None = None
) -> SkillGap:
    """``ValueError`` for an unknown status — after the row lookup, so a
    stranger's row stays a ``NotFound`` whatever the value sent."""
    store = _store(persistence)
    gap = store.skill_gaps.get(owner, pk)
    if status not in GapStatus.values:
        raise ValueError("État inconnu.")
    gap.status = status
    store.skill_gaps.save(gap, fields=["status"])
    return gap


# ---------------------------------------------------------------------------
# Read models built from the ports (the pipeline board, the counters)
# ---------------------------------------------------------------------------


def board(owner, *, persistence: Persistence | None = None) -> list[dict]:
    """Open applications grouped into the pipeline columns, in order."""
    buckets: dict[str, list[Application]] = {status: [] for status in PIPELINE_STATUSES}
    for application in _store(persistence).applications.by_status(owner, PIPELINE_STATUSES):
        buckets[application.status].append(application)
    return [
        {
            "status": status,
            "label": Status(status).label,
            "tone": STATUS_TONE.get(status, "slate"),
            "applications": buckets[status],
            "count": len(buckets[status]),
        }
        for status in PIPELINE_STATUSES
    ]


def dashboard_stats(
    owner, *, persistence: Persistence | None = None, today: dt.date | None = None
) -> dict:
    """The figures of the stats bar and the funnel, from two aggregates."""
    store = _store(persistence)
    counts = store.applications.status_counts(owner)

    def count_of(*statuses) -> int:
        return sum(counts.get(s, 0) for s in statuses)

    sent_or_beyond = count_of(
        *IN_FLIGHT_STATUSES, Status.ACCEPTED, Status.REJECTED, Status.GHOSTED
    )
    answered = count_of(*INTERVIEWING_STATUSES, Status.OFFER, Status.ACCEPTED, Status.REJECTED)
    scores = store.applications.scores(owner, OPEN_STATUSES)

    return {
        "tracked": sum(n for status, n in counts.items() if status != Status.DISCARDED),
        "to_apply": count_of(Status.TO_APPLY),
        "backlog": count_of(Status.BACKLOG),
        "in_flight": count_of(*IN_FLIGHT_STATUSES),
        "interviewing": count_of(*INTERVIEWING_STATUSES),
        "offers": count_of(Status.OFFER, Status.ACCEPTED),
        "rejected": count_of(Status.REJECTED),
        "discarded": count_of(Status.DISCARDED),
        "sent_total": sent_or_beyond,
        "answered": answered,
        "response_rate": round(answered / sent_or_beyond * 100) if sent_or_beyond else None,
        "average_score": round(sum(scores) / len(scores)) if scores else None,
        "today": _today(today),
    }


def attention(
    owner, *, stale_days: int, today: dt.date, persistence: Persistence | None = None
) -> list[Application]:
    """What needs a move today: due follow-ups first, then stale applications."""
    return _store(persistence).applications.needing_attention(
        owner, stale_days=stale_days, on=today
    )


def nav_counters(
    owner, *, stale_days: int, today: dt.date, persistence: Persistence | None = None
) -> dict[str, int]:
    """The badges of the navigation rail, from aggregates only."""
    store = _store(persistence)
    counts = store.applications.status_counts(owner)
    return {
        "open": sum(counts.get(status, 0) for status in OPEN_STATUSES),
        "tracked": sum(n for status, n in counts.items() if status != Status.DISCARDED),
        "attention": store.applications.attention_count(owner, stale_days=stale_days, on=today),
        "documents": store.documents.count(owner),
    }
