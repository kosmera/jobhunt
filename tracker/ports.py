"""Driven ports: what the use cases need from persistence, and nothing more.

The entities are the model classes themselves (``Application``, ``Company``…),
so templates keep receiving what they always did. What crosses a port is an
instance, a list, an int or a dict — never a queryset. Every method that
takes an ``owner`` scopes on that account (``owner.pk``): another account's
row is indistinguishable from a missing one and raises ``NotFound``.

Two conventions every adapter honours, checked by the contract tests:

- ordering: ``None`` sorts last on every key (score, dates), whatever the
  engine; "by name" means ascending by name in the engine's collation and
  nothing finer;
- thresholds: ``stale_days`` and ``follow_up_days`` are always supplied by
  the caller. Adapters and services never read the owner's preferences.

Page reads (filters, dashboards, breakdowns) are not ports: they live in
``tracker.queries``, owned by the web adapter, and may use the ORM directly.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from contextlib import AbstractContextManager
from typing import Protocol

from tracker.models import ActivityEvent, Application, Company, Contact, Document, SkillGap


class NotFound(LookupError):
    """No such row for that account."""


class ApplicationRepository(Protocol):
    def get(self, owner, pk: int) -> Application:
        """One application, with ``company`` and ``source_platform`` loaded
        (the ORM adapter also joins ``owner.preferences``, which the model's
        own ``apply_status``/``is_stale`` read)."""

    def detail(self, owner, pk: int) -> Application:
        """``get`` plus the documents, contacts and events the detail page lists."""

    def add(self, application: Application) -> Application:
        """Persist a new instance (pk, slug and timestamps assigned)."""

    def save(self, application: Application, *, fields: Iterable[str] | None = None) -> None:
        """Write the instance back; only ``fields`` (plus ``updated_at``) when given."""

    def remove(self, application: Application) -> None:
        """Delete the application and everything hanging off it."""

    def by_status(self, owner, statuses: Iterable[str]) -> list[Application]:
        """Order: score descending (unscored last), company name, pk."""

    def status_counts(self, owner) -> dict[str, int]:
        """``{status: number of applications}`` for statuses that have rows."""

    def needing_attention(self, owner, *, stale_days: int, on: dt.date) -> list[Application]:
        """Due follow-ups and stale sent applications; order: follow-up date
        ascending (none last), then application date ascending (none last)."""

    def attention_count(self, owner, *, stale_days: int, on: dt.date) -> int:
        """``len(needing_attention(...))`` without loading the rows."""

    def scores(self, owner, statuses: Iterable[str]) -> list[int]:
        """The scores that are set, on active applications in ``statuses``."""


class CompanyRepository(Protocol):
    def get_or_create(self, owner, name: str, sector: str | None = None) -> Company:
        """The account's company of that name (case-insensitive, stripped),
        created on first use; a given ``sector`` updates an existing row.
        Returns a persisted instance."""


class EventRepository(Protocol):
    def get(self, owner, pk: int) -> ActivityEvent:
        """Scoped through the application's owner; ``application`` loaded."""

    def add(
        self,
        application: Application,
        kind: str,
        title: str,
        detail: str = "",
        on: dt.date | None = None,
    ) -> ActivityEvent:
        """Record a timeline entry, dated today unless ``on`` is given."""

    def remove(self, event: ActivityEvent) -> None: ...


class DocumentRepository(Protocol):
    def get(self, owner, pk: int) -> Document:
        """``application`` loaded when the document is attached."""

    def add(self, document: Document) -> Document:
        """Persist a new document; an attached one inherits the application's owner."""

    def remove(self, document: Document) -> None: ...

    def demote_primary(self, application: Application, kind: str) -> None:
        """Clear ``is_primary`` on the application's documents of that kind."""

    def count(self, owner) -> int: ...


class ContactRepository(Protocol):
    def get(self, owner, pk: int) -> Contact:
        """Scoped through the application's owner; ``application`` loaded."""

    def add(self, contact: Contact) -> Contact: ...

    def remove(self, contact: Contact) -> None: ...


class SkillGapRepository(Protocol):
    def get(self, owner, pk: int) -> SkillGap: ...

    def save(self, gap: SkillGap, *, fields: Iterable[str] | None = None) -> None:
        """Write the instance back; only ``fields`` when given."""


class Persistence(Protocol):
    """The set of repositories one adapter provides, plus its unit of work."""

    applications: ApplicationRepository
    companies: CompanyRepository
    events: EventRepository
    documents: DocumentRepository
    contacts: ContactRepository
    skill_gaps: SkillGapRepository

    def atomic(self) -> AbstractContextManager:
        """All-or-nothing scope for a use case that writes several rows."""
