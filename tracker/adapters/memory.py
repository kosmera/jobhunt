"""An in-memory adapter: the same ports, backed by dicts.

One ``MemoryPersistence`` instance is one store; nothing is shared between
instances. It exists to prove the ports are a real boundary and to run the
use cases and rules without a database (``SimpleTestCase``): fixtures are
unsaved instances — ``User(pk=1, …)``, ``Company(pk=1, owner=user, …)``,
``Application(owner=user, company=company, …)`` — handed to ``add``.

What the adapter takes care of, mirroring the model hooks: ``add`` assigns
a pk, a slug (unique within the store) and the timestamps; an attached
document inherits the application's owner; ``remove`` on an application
cascades to its documents, contacts and events; every sort puts ``None``
last, as the ORM adapter does with ``nulls_last``.

OFF-LIMITS on instances living in this store, because they reach the
configured database (and raise ``DatabaseOperationForbidden`` under a
``SimpleTestCase`` — an intentional guard, not a bug): the reverse managers
``documents`` / ``contacts`` / ``events``, ``primary_cv``, the properties
``is_stale`` and ``needs_attention``, ``owner_preferences``, ``save``,
``delete``, ``log``, ``apply_status`` and ``Company.save`` (unique slug).
Use the ports and ``tracker.domain`` instead; ``rows()`` on a repository
exposes the stored instances for assertions.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Iterable
from contextlib import nullcontext

from django.utils import timezone
from django.utils.text import slugify

from tracker import domain
from tracker.models import (
    ActivityEvent,
    Application,
    Company,
    Contact,
    Document,
    Sector,
    SkillGap,
    Status,
)
from tracker.ports import NotFound


def _nulls_last(value, *, descending: bool = False):
    """A sort key that sends ``None`` to the end whatever the direction."""
    if value is None:
        return (1, 0)
    return (0, -value if descending else value)


def _date_nulls_last(value: dt.date | None):
    return (1, dt.date.max) if value is None else (0, value)


class _Table:
    """A dict of rows keyed by pk, handing out pks on ``insert``.

    The live instance is the row, but what counts as *saved* is the snapshot
    taken by ``insert``/``update``: every read re-applies it, so an attribute
    changed on the instance and not written through ``save(fields=…)`` is
    gone on the next read — exactly what the ORM adapter does with
    ``update_fields``. Without that, a use case forgetting to list a field it
    touched would pass every in-memory test and lose the write for real.
    """

    def __init__(self):
        self.rows: dict[int, object] = {}
        self._saved: dict[int, dict[str, object]] = {}
        self._related: dict[int, dict[str, object]] = {}
        self._next = 1

    def insert(self, row):
        """Store ``row`` whole: a new row, or a full save of a stored one."""
        if row.pk is None:
            row.pk = self._next
        elif self.rows.get(row.pk) not in (None, row):
            raise ValueError(f"Clé {row.pk} déjà prise dans ce magasin.")
        # An explicit pk must never be handed out again.
        self._next = max(self._next, row.pk + 1)
        self.rows[row.pk] = row
        self._snapshot(row)
        return row

    def update(self, row, names):
        """Persist only the fields called ``names`` of a stored row."""
        if row.pk not in self.rows:
            return self.insert(row)
        saved, related = self._saved[row.pk], self._related[row.pk]
        for name in names:
            field = row._meta.get_field(name)
            saved[field.attname] = getattr(row, field.attname)
            if field.is_relation and field.name in row._state.fields_cache:
                related[field.name] = row._state.fields_cache[field.name]
        return row

    def _snapshot(self, row):
        self._saved[row.pk] = {
            field.attname: getattr(row, field.attname) for field in row._meta.concrete_fields
        }
        self._related[row.pk] = dict(row._state.fields_cache)

    def _hydrate(self, row):
        # ``__dict__`` rather than ``setattr``: the foreign-key descriptor
        # would drop the cached related object and the next access would
        # reach the database.
        row.__dict__.update(self._saved[row.pk])
        row._state.fields_cache = dict(self._related[row.pk])
        return row

    def __iter__(self):
        return (self._hydrate(row) for row in list(self.rows.values()))

    def get(self, pk):
        row = self.rows.get(pk)
        return None if row is None else self._hydrate(row)

    def pop(self, pk):
        self.rows.pop(pk, None)
        self._saved.pop(pk, None)
        self._related.pop(pk, None)


class MemoryApplications:
    def __init__(self, store: MemoryPersistence):
        self._store = store
        self._table = _Table()

    def rows(self) -> list[Application]:
        return list(self._table)

    def _owned(self, owner) -> list[Application]:
        return [row for row in self._table if row.owner_id == owner.pk]

    def get(self, owner, pk: int) -> Application:
        application = self._table.get(pk)
        if application is None or application.owner_id != owner.pk:
            raise NotFound(f"Candidature {pk} introuvable pour ce compte.")
        return application

    detail = get

    def add(self, application: Application) -> Application:
        if (
            application.company_id
            and application.owner_id
            and application.company.owner_id != application.owner_id
        ):
            # The same guard as ``Application.save``.
            raise ValueError("La société et la candidature n'ont pas le même propriétaire.")
        if not application.slug:
            base = slugify(f"{application.company.name}-{application.title}")[:200] or "candidature"
            application.slug = self._store.unique_slug(base, (row.slug for row in self._table))
        now = timezone.now()
        application.created_at = application.created_at or now
        application.updated_at = now
        return self._table.insert(application)

    def save(self, application: Application, *, fields: Iterable[str] | None = None) -> None:
        application.updated_at = timezone.now()
        if fields is None:
            self._table.insert(application)
        else:
            self._table.update(application, [*fields, "updated_at"])

    def remove(self, application: Application) -> None:
        self._table.pop(application.pk)
        self._store.cascade(application)

    def by_status(self, owner, statuses: Iterable[str]) -> list[Application]:
        wanted = set(statuses)
        rows = [row for row in self._owned(owner) if row.status in wanted]
        return sorted(
            rows,
            key=lambda row: (_nulls_last(row.score, descending=True), row.company.name, row.pk),
        )

    def status_counts(self, owner) -> dict[str, int]:
        return dict(Counter(row.status for row in self._owned(owner)))

    def needing_attention(self, owner, *, stale_days: int, on: dt.date) -> list[Application]:
        rows = [
            row
            for row in self._owned(owner)
            if domain.needs_attention(row, stale_days=stale_days, today=on)
        ]
        return sorted(
            rows,
            key=lambda row: (
                _date_nulls_last(row.follow_up_on),
                _date_nulls_last(row.applied_on),
                row.pk,
            ),
        )

    def attention_count(self, owner, *, stale_days: int, on: dt.date) -> int:
        return len(self.needing_attention(owner, stale_days=stale_days, on=on))

    def scores(self, owner, statuses: Iterable[str]) -> list[int]:
        wanted = set(statuses)
        return [
            row.score
            for row in self._owned(owner)
            if row.status in wanted and row.status != Status.DISCARDED and row.score is not None
        ]


class MemoryCompanies:
    def __init__(self, store: MemoryPersistence):
        self._store = store
        self._table = _Table()

    def rows(self) -> list[Company]:
        return list(self._table)

    def get_or_create(self, owner, name: str, sector: str | None = None) -> Company:
        name = name.strip()
        for company in self._table:
            if company.owner_id == owner.pk and company.name.lower() == name.lower():
                if sector and company.sector != sector:
                    company.sector = sector
                    self._table.update(company, ["sector"])
                return company
        company = Company(owner=owner, name=name, sector=sector or Sector.UNKNOWN)
        company.slug = self._store.unique_slug(
            slugify(name) or "societe", (row.slug for row in self._table)
        )
        return self._table.insert(company)


class MemoryEvents:
    def __init__(self, store: MemoryPersistence):
        self._table = _Table()

    def rows(self) -> list[ActivityEvent]:
        return list(self._table)

    def get(self, owner, pk: int) -> ActivityEvent:
        event = self._table.get(pk)
        if event is None or event.application.owner_id != owner.pk:
            raise NotFound(f"Événement {pk} introuvable pour ce compte.")
        return event

    def add(
        self,
        application: Application,
        kind: str,
        title: str,
        detail: str = "",
        on: dt.date | None = None,
    ) -> ActivityEvent:
        event = ActivityEvent(
            application=application,
            kind=kind,
            title=title,
            detail=detail,
            happened_on=on or timezone.localdate(),
        )
        event.created_at = timezone.now()
        return self._table.insert(event)

    def remove(self, event: ActivityEvent) -> None:
        self._table.pop(event.pk)

    def _drop_for(self, application: Application) -> None:
        for event in [row for row in self._table if row.application_id == application.pk]:
            self._table.pop(event.pk)


class MemoryDocuments:
    def __init__(self, store: MemoryPersistence):
        self._store = store
        self._table = _Table()

    def rows(self) -> list[Document]:
        return list(self._table)

    def get(self, owner, pk: int) -> Document:
        document = self._table.get(pk)
        if document is None or document.owner_id != owner.pk:
            raise NotFound(f"Document {pk} introuvable pour ce compte.")
        return document

    def add(self, document: Document) -> Document:
        if document.application_id:
            # The owner comes from the store's row, not from the descriptor,
            # which would query the database for an application it has not
            # cached.
            application = self._store.applications._table.get(document.application_id)
            owner_id = (application or document.application).owner_id
            if not document.owner_id:
                document.owner_id = owner_id
            elif document.owner_id != owner_id:
                raise ValueError("Le document et la candidature n'ont pas le même propriétaire.")
        document.uploaded_at = document.uploaded_at or timezone.now()
        return self._table.insert(document)

    def remove(self, document: Document) -> None:
        self._table.pop(document.pk)

    def demote_primary(self, application: Application, kind: str) -> None:
        for document in self._table:
            if document.application_id == application.pk and document.kind == kind:
                document.is_primary = False
                self._table.update(document, ["is_primary"])

    def count(self, owner) -> int:
        return sum(1 for row in self._table if row.owner_id == owner.pk)

    def _drop_for(self, application: Application) -> None:
        for document in [row for row in self._table if row.application_id == application.pk]:
            self._table.pop(document.pk)


class MemoryContacts:
    def __init__(self, store: MemoryPersistence):
        self._table = _Table()

    def rows(self) -> list[Contact]:
        return list(self._table)

    def get(self, owner, pk: int) -> Contact:
        contact = self._table.get(pk)
        if contact is None or contact.application.owner_id != owner.pk:
            raise NotFound(f"Contact {pk} introuvable pour ce compte.")
        return contact

    def add(self, contact: Contact) -> Contact:
        return self._table.insert(contact)

    def remove(self, contact: Contact) -> None:
        self._table.pop(contact.pk)

    def _drop_for(self, application: Application) -> None:
        for contact in [row for row in self._table if row.application_id == application.pk]:
            self._table.pop(contact.pk)


class MemorySkillGaps:
    def __init__(self, store: MemoryPersistence):
        self._table = _Table()

    def rows(self) -> list[SkillGap]:
        return list(self._table)

    def get(self, owner, pk: int) -> SkillGap:
        gap = self._table.get(pk)
        if gap is None or gap.owner_id != owner.pk:
            raise NotFound(f"Lacune {pk} introuvable pour ce compte.")
        return gap

    def save(self, gap: SkillGap, *, fields: Iterable[str] | None = None) -> None:
        if fields is None:
            self._table.insert(gap)
        else:
            self._table.update(gap, list(fields))


class MemoryPersistence:
    """One store per instance; ``atomic()`` is a no-op scope."""

    def __init__(self):
        self.applications = MemoryApplications(self)
        self.companies = MemoryCompanies(self)
        self.events = MemoryEvents(self)
        self.documents = MemoryDocuments(self)
        self.contacts = MemoryContacts(self)
        self.skill_gaps = MemorySkillGaps(self)

    def atomic(self):
        return nullcontext()

    # -- helpers shared by the repositories --------------------------------

    @staticmethod
    def unique_slug(base: str, taken: Iterable[str]) -> str:
        """``base``, suffixed with a counter while the slug is already used."""
        used = set(taken)
        base = base[:200] or "item"
        candidate, counter = base, 2
        while candidate in used:
            suffix = f"-{counter}"
            candidate = f"{base[: 200 - len(suffix)]}{suffix}"
            counter += 1
        return candidate

    def cascade(self, application: Application) -> None:
        """What ``on_delete=CASCADE`` would do for the application's satellites."""
        self.events._drop_for(application)
        self.documents._drop_for(application)
        self.contacts._drop_for(application)
