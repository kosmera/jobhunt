"""Use cases: what the interface can do to an application, in one place.

Each function takes the entities it works on, the thresholds it needs as
plain ints (``follow_up_days``, ``stale_days`` — the caller reads the owner's
preferences, never this module), an optional ``today`` and the
``persistence`` to write through (the configured adapter by default). The
rules themselves live in ``tracker.domain``; this module sequences them
with the ports and draws the transaction boundaries.

Files go through the storage port the same way (``storage``, the configured
adapter by default): a use case is the only writer of a document's bytes,
and the AI layer receives the anonymised text the port produces — never a
file, never a path.

Nothing here knows about requests, templates, the ORM or a cloud SDK, so
every use case runs unchanged on ``MemoryPersistence`` and
``MemoryStorageAdapter`` in a database-free test.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import PurePosixPath

from django.core.files.base import File
from django.utils import timezone

from tracker import domain
from tracker.adapters import persistence as configured_persistence
from tracker.adapters import storage as configured_storage
from tracker import privacy
from tracker.adapters.document_text import UnsupportedFormat
from tracker.models import (
    DOCUMENT_NAME_MAX_LENGTH,
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
    document_upload_to,
)
from tracker.ports import CVAnalyzer, Persistence, StoragePort
from tracker.privacy import AnonymizedText


logger = logging.getLogger(__name__)


def _store(persistence: Persistence | None) -> Persistence:
    return configured_persistence() if persistence is None else persistence


def _files(storage: StoragePort | None) -> StoragePort:
    return configured_storage() if storage is None else storage


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
    upload: File | None = None,
    persistence: Persistence | None = None,
    storage: StoragePort | None = None,
) -> Document:
    """File a document under an application, or in the library (``None``).

    ``upload`` is the file's content: it is stored through the storage port
    under the name the model's layout dictates, *before* the row is written,
    and removed again if writing the row fails. Without ``upload`` the
    document's file is taken as already stored (an extension that saved it
    itself). A new primary document of a kind demotes the previous one; an
    attached document leaves a trace on the timeline.
    """
    store = _store(persistence)
    files = _files(storage)
    document.owner = owner
    document.application = application
    document.label = label
    stored_name: str | None = None
    if upload is not None:
        wanted = document_upload_to(document, PurePosixPath(upload.name or "document").name)
        # Read before the write: once the field holds a name it is a stored
        # file, and asking its size again would be a round-trip.
        size = upload.size
        stored_name = files.save_file(wanted, upload, max_length=DOCUMENT_NAME_MAX_LENGTH)
        # A name assigned to the field is a file already in storage: the ORM
        # adapter's save writes nothing more.
        document.file = stored_name
        document.size_bytes = size
    try:
        with store.atomic():
            if document.is_primary and application is not None:
                store.documents.demote_primary(application, document.kind)
            store.documents.add(document)
            if application is not None:
                store.events.add(application, EventKind.NOTE, f"Document ajouté : {document.label}")
    except BaseException:
        if stored_name is not None:
            _forget_file(files, stored_name)
        raise
    return document


def _forget_file(files: StoragePort, file_name: str) -> None:
    """Best effort: the row is gone (or never written), a storage outage must
    not turn that into an error the caller sees."""
    with suppress(OSError):
        files.delete_file(file_name)


#: Below this many characters of extracted text, a document is not a CV the
#: model could read: a scan without a text layer, or a heavily laid-out PDF.
#: It is stored and left unanalysed — the core never sends the file itself.
MIN_TEXT_LENGTH = 200


@dataclass(frozen=True)
class CVIntake:
    """What ``ingest_cv`` did: the stored document, the anonymised text (``None``
    when the format cannot be read, or when nobody analyses CVs) and whether
    the AI layer received it."""

    document: Document
    anonymized: AnonymizedText | None
    analyzed: bool


def ingest_cv(
    owner,
    application: Application | None,
    document: Document,
    *,
    upload: File,
    label: str,
    known: Mapping[str, str] | None = None,
    analyzer: CVAnalyzer | None,
    persistence: Persistence | None = None,
    storage: StoragePort | None = None,
) -> CVIntake:
    """Store an uploaded CV, file it, and hand its *anonymised* text to the AI layer.

    Everything about the file goes through the storage port: the bytes are
    written by ``attach_document``, the text is read back and anonymised by
    ``extract_and_anonymize_text`` (``known``: what the profile can vouch
    for — name, e-mail, home town — see ``tracker.privacy.known_identity``),
    and ``analyzer`` gets that text — plus the document's id and its label,
    anonymised the same way — never the file. A CV nobody can read (an unsupported format, a scan without a
    text layer, less than ``MIN_TEXT_LENGTH`` characters) is stored all the
    same and simply not analysed: there is no fallback that sends the file.
    An analyser that fails, or a provider that blinks while the text is read
    back, is logged and leaves the document filed — an upload is never lost
    because the analysis was not possible.
    """
    files = _files(storage)
    document = attach_document(
        owner, application, document, label=label, upload=upload,
        persistence=persistence, storage=files,
    )
    if analyzer is None:
        return CVIntake(document, None, False)
    try:
        anonymized = files.extract_and_anonymize_text(document.file.name or "", known=known)
    except UnsupportedFormat:
        return CVIntake(document, None, False)
    except OSError:
        # The provider blinked between the write and the read. The CV is
        # stored, which is what the owner asked for; losing the request here
        # would roll their upload back and strand the bytes.
        logger.exception("Texte du document %s illisible ; analyse abandonnée.", document.pk)
        return CVIntake(document, None, False)
    if len(anonymized.text.strip()) < MIN_TEXT_LENGTH:
        # Too little text to be a CV the model could read: a scan, or a
        # layout the extractor could not follow. Stored, not analysed — the
        # core has no fallback that would send the file itself.
        return CVIntake(document, anonymized, False)
    try:
        analyzer.analyze_cv(
            owner,
            document_id=document.pk,
        # The label defaults to the uploaded file name — « CV Lionel Dupont
        # Nivelles.pdf » — which is the most name-bearing string in the whole
        # flow. It crosses the port scrubbed like the text; the stored label
        # stays as it is, for its owner's own eyes.
            label=privacy.anonymize(document.label, known=known).text,
            language=document.language,
            text=anonymized,
        )
    except Exception:
        # An extension is not the core's correctness: whatever it raises, the
        # document stays filed and the page answers normally.
        logger.exception("La couche IA a refusé le document %s.", document.pk)
        return CVIntake(document, anonymized, False)
    return CVIntake(document, anonymized, True)


def delete_document(
    owner, pk: int, *, persistence: Persistence | None = None, storage: StoragePort | None = None
) -> Document:
    """Remove a document and its file; returns the removed instance (its
    ``application``, ``None`` for a library document, and ``label`` feed the
    response). The file goes through the storage port, once the rows agree:
    a rollback further up (on PostgreSQL the whole request is one
    transaction) must not leave a document row without its bytes. The ORM
    adapter's ``post_delete`` receiver defers to the same moment, so the two
    are one idempotent deletion."""
    store = _store(persistence)
    files = _files(storage)
    document = store.documents.get(owner, pk)
    file_name = document.file.name or ""
    store.documents.remove(document)
    if file_name:
        store.on_commit(lambda: _forget_file(files, file_name))
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
