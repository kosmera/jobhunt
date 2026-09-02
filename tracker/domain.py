"""Business rules of the tracker, with no persistence in sight.

Everything here works on plain model instances (they are the entities) and
never touches the database: the rules can be exercised with unsaved objects
in a ``SimpleTestCase``. Thresholds that depend on the owner's preferences
(``stale_days``, ``follow_up_days``) are always handed in by the caller —
either as an int or as a zero-argument callable, resolved only when the rule
actually needs the value so a row that cannot be stale never costs a lookup.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from tracker.models import CLOSED_STATUSES, IN_FLIGHT_STATUSES, Application, EventKind, Status

#: The timeline entry a transition produces when it is more than a plain
#: status change.
TRANSITION_EVENT_KINDS = {
    Status.SENT: EventKind.APPLIED,
    Status.INTERVIEW: EventKind.INTERVIEW,
    Status.SCREENING: EventKind.CALL,
    Status.TECHNICAL: EventKind.TEST,
    Status.OFFER: EventKind.OFFER,
    Status.REJECTED: EventKind.REJECTION,
}

Threshold = int | Callable[[], int]


def _resolve(threshold: Threshold) -> int:
    return threshold() if callable(threshold) else threshold


@dataclass(frozen=True)
class Transition:
    """What a status change touched, for whoever persists it.

    ``fields`` are the attributes assigned on the instance (``status`` always,
    then the dates that were set — including those set to ``None``);
    ``event_kind``/``event_title`` describe the timeline entry to record.
    """

    fields: tuple[str, ...]
    event_kind: str
    event_title: str


def plan_transition(
    application: Application,
    new_status: str,
    *,
    today: dt.date,
    follow_up_days: Threshold,
) -> Transition | None:
    """Move ``application`` to ``new_status`` in memory, keeping dates in step.

    Returns ``None`` when nothing changes (same status, or a value that is
    not a status at all). The follow-up delay is only resolved when a
    freshly sent application has no reminder yet.
    """
    if new_status == application.status or new_status not in Status.values:
        return None

    previous = Status(application.status).label
    application.status = new_status
    fields = ["status"]

    if new_status in IN_FLIGHT_STATUSES and not application.applied_on:
        application.applied_on = today
        fields.append("applied_on")
    if new_status == Status.SENT and not application.follow_up_on:
        application.follow_up_on = today + dt.timedelta(days=_resolve(follow_up_days))
        fields.append("follow_up_on")
    if new_status in CLOSED_STATUSES:
        application.closed_on = application.closed_on or today
        application.follow_up_on = None
        fields.extend(["closed_on", "follow_up_on"])
    else:
        application.closed_on = None
        fields.append("closed_on")

    return Transition(
        fields=tuple(dict.fromkeys(fields)),
        event_kind=TRANSITION_EVENT_KINDS.get(new_status, EventKind.STATUS),
        event_title=f"{previous} → {Status(new_status).label}",
    )


def is_stale(application: Application, *, stale_days: Threshold, today: dt.date) -> bool:
    """Sent ``stale_days`` ago or more, still no reaction from the other side."""
    if application.status != Status.SENT or application.applied_on is None:
        return False
    return (today - application.applied_on).days >= _resolve(stale_days)


def follow_up_is_due(application: Application, *, today: dt.date) -> bool:
    """A reminder set for today or earlier, while the ball is still in play."""
    return (
        application.follow_up_on is not None
        and application.follow_up_on <= today
        and application.status in IN_FLIGHT_STATUSES
    )


def needs_attention(application: Application, *, stale_days: Threshold, today: dt.date) -> bool:
    """A follow-up that is due, or an application gone stale — the same
    predicate as ``ApplicationQuerySet.needs_attention``."""
    return follow_up_is_due(application, today=today) or is_stale(
        application, stale_days=stale_days, today=today
    )
