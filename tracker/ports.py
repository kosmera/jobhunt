"""Driven ports: what the use cases need from the outside, and nothing more.

Two families. **Persistence** — the repositories and their unit of work, one
adapter per engine. **Files** — where an uploaded CV goes and how it comes
back (``StoragePort``): the provider (disk, Azure Blob Storage, memory) is
configuration, and the AI layer only ever receives the *anonymised* text the
port produces (``CVAnalyzer``).

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
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager
from typing import IO, Any, Protocol, runtime_checkable

from django.core.files.base import File

from tracker.models import ActivityEvent, Application, Company, Contact, Document, SkillGap
from tracker.privacy import AnonymizedText


class NotFound(LookupError):
    """No such row for that account."""


class StorageError(OSError):
    """The file provider failed (network, credentials, quota…).

    An ``OSError`` so the code paths that already survive a missing local
    file (``Document.size_display``, the download view) survive a provider
    outage the same way; the adapters translate their SDK's exceptions.
    """


class MissingFile(FileNotFoundError):
    """No file stored under that name (``FileNotFoundError`` ⊂ ``OSError``)."""


class ApplicationRepository(Protocol):
    def get(self, owner, pk: int) -> Application:
        """One application, with ``company`` and ``source_platform`` loaded
        (the ORM adapter also joins ``owner.preferences``, which the model's
        own ``apply_status``/``is_stale`` read)."""
        ...

    def detail(self, owner, pk: int) -> Application:
        """``get`` plus the documents, contacts and events the detail page lists."""
        ...

    def add(self, application: Application) -> Application:
        """Persist a new instance (pk, slug and timestamps assigned)."""
        ...

    def save(self, application: Application, *, fields: Iterable[str] | None = None) -> None:
        """Write the instance back; only ``fields`` (plus ``updated_at``) when given."""
        ...

    def remove(self, application: Application) -> None:
        """Delete the application and everything hanging off it."""
        ...

    def by_status(self, owner, statuses: Iterable[str]) -> list[Application]:
        """Order: score descending (unscored last), company name, pk."""
        ...

    def status_counts(self, owner) -> dict[str, int]:
        """``{status: number of applications}`` for statuses that have rows."""
        ...

    def needing_attention(self, owner, *, stale_days: int, on: dt.date) -> list[Application]:
        """Due follow-ups and stale sent applications; order: follow-up date
        ascending (none last), then application date ascending (none last)."""
        ...

    def attention_count(self, owner, *, stale_days: int, on: dt.date) -> int:
        """``len(needing_attention(...))`` without loading the rows."""
        ...

    def scores(self, owner, statuses: Iterable[str]) -> list[int]:
        """The scores that are set, on active applications in ``statuses``."""
        ...


class CompanyRepository(Protocol):
    def get_or_create(self, owner, name: str, sector: str | None = None) -> Company:
        """The account's company of that name (case-insensitive, stripped),
        created on first use; a given ``sector`` updates an existing row.
        Returns a persisted instance."""
        ...


class EventRepository(Protocol):
    def get(self, owner, pk: int) -> ActivityEvent:
        """Scoped through the application's owner; ``application`` loaded."""
        ...

    def add(
        self,
        application: Application,
        kind: str,
        title: str,
        detail: str = "",
        on: dt.date | None = None,
    ) -> ActivityEvent:
        """Record a timeline entry, dated today unless ``on`` is given."""
        ...

    def remove(self, event: ActivityEvent) -> None: ...


class DocumentRepository(Protocol):
    def get(self, owner, pk: int) -> Document:
        """``application`` loaded when the document is attached."""
        ...

    def add(self, document: Document) -> Document:
        """Persist a new document; an attached one inherits the application's owner."""
        ...

    def remove(self, document: Document) -> None: ...

    def demote_primary(self, application: Application, kind: str) -> None:
        """Clear ``is_primary`` on the application's documents of that kind."""
        ...

    def count(self, owner) -> int: ...

    def owns_file(self, owner, file_name: str) -> bool:
        """Whether that stored file belongs to a document of that account.

        What the link view asks before streaming anything: a signed link is
        checked against the rows, not only against its own signature."""
        ...


class ContactRepository(Protocol):
    def get(self, owner, pk: int) -> Contact:
        """Scoped through the application's owner; ``application`` loaded."""
        ...

    def add(self, contact: Contact) -> Contact: ...

    def remove(self, contact: Contact) -> None: ...


class SkillGapRepository(Protocol):
    def get(self, owner, pk: int) -> SkillGap: ...

    def save(self, gap: SkillGap, *, fields: Iterable[str] | None = None) -> None:
        """Write the instance back; only ``fields`` when given."""
        ...


class Persistence(Protocol):
    """The set of repositories one adapter provides, plus its unit of work.

    Read-only properties rather than attributes: a writable protocol member
    is invariant, and an adapter's ``applications`` is a *subtype* of
    ``ApplicationRepository``.
    """

    @property
    def applications(self) -> ApplicationRepository: ...

    @property
    def companies(self) -> CompanyRepository: ...

    @property
    def events(self) -> EventRepository: ...

    @property
    def documents(self) -> DocumentRepository: ...

    @property
    def contacts(self) -> ContactRepository: ...

    @property
    def skill_gaps(self) -> SkillGapRepository: ...

    def atomic(self) -> AbstractContextManager[Any]:
        """All-or-nothing scope for a use case that writes several rows."""
        ...

    def on_commit(self, callback: Callable[[], Any]) -> None:
        """Run ``callback`` once the current transaction is committed — now,
        when there is none.

        What a use case needs to touch the outside world (deleting a file)
        only after the rows agree: a rollback further up must not leave a
        document row without its bytes.
        """
        ...


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

#: File names are ``documents/<owner_id>/<application slug | bibliotheque>/<basename>``
#: (``tracker.models.document_upload_to``): the account is part of the name,
#: so a link minted for a name can be scoped to that account without the
#: port knowing about users.
FILES_PREFIX = "documents/"


@runtime_checkable
class StoragePort(Protocol):
    """Where the bytes of a document live. One adapter per provider; the
    configured one doubles as the Django storage behind ``Document.file`` so
    the model field, the download view and the use cases share one object.

    Names are POSIX-style relative paths as produced by ``upload_to``; the
    port sanitises the basename and suffixes it when the name is taken, and
    returns the name actually used. Whatever the provider, a method raises
    only these two: ``MissingFile`` for a name nothing is stored under (a
    name no storage would accept counts as one) and ``StorageError`` for a
    provider that failed. Both are ``OSError``, so a page that already
    survived a missing local file survives an outage too.
    """

    @property
    def served_by_app(self) -> bool:
        """``True`` when the application streams the bytes itself (disk,
        memory): the download view then opens the file. ``False`` when they
        live elsewhere (Azure): the view redirects to ``get_secure_url``."""
        ...

    def save_file(
        self, file_name: str, content: bytes | IO[bytes] | File, *, max_length: int | None = None
    ) -> str:
        """Store ``content`` under ``file_name`` (or a free variant); returns
        the stored name, no longer than ``max_length`` when given (the width
        of the column that will hold it)."""
        ...

    def open_file(self, file_name: str) -> File:
        """A binary read handle (``read``, ``close``, ``size``); close it."""
        ...

    def delete_file(self, file_name: str) -> None:
        """Remove the file; a name that is already gone is not an error."""
        ...

    def file_exists(self, file_name: str) -> bool: ...

    def file_size(self, file_name: str) -> int:
        """The stored size in bytes."""
        ...

    def file_modified_at(self, file_name: str) -> dt.datetime:
        """When the file was last written, as an aware datetime."""
        ...

    def list_files(self, folder: str = "") -> Iterator[str]:
        """Every stored name under that folder, recursively, in no order.

        A folder, not a string prefix: ``"documents"`` yields
        ``documents/7/…`` and never ``documents-old/…``. Empty means
        everything the storage holds.

        For housekeeping, not for pages: it walks the whole subtree. What it
        makes possible is finding the files no row points at any more — a
        request rolled back after its bytes were written, or a process
        killed between the two.
        """
        ...

    def get_secure_url(self, file_name: str) -> str:
        """A short-lived link to the file (``settings.STORAGES`` ``link_ttl``,
        five minutes by default), meant for the file's own account.

        Local and memory: the URL of a login-protected view that streams the
        file — signed, expiring, and checked against the rows before it
        serves anything, so a borrowed link is a 404. Azure: a read-only SAS
        URL, a true bearer link that whoever holds it can follow until it
        expires. Hand it to the owner only, on either provider.
        """
        ...

    def extract_and_anonymize_text(
        self, file_name: str, *, known: Mapping[str, str] | None = None
    ) -> AnonymizedText:
        """The document's text with direct identifiers replaced by placeholders
        (``tracker.privacy``). ``known`` adds what the profile can vouch for
        (name, e-mail, home town). ``UnsupportedFormat`` for a file the
        extractor cannot read."""
        ...


class CVAnalyzer(Protocol):
    """The AI layer, as the core sees it: it gets the anonymised text, never
    the file. ``document_id`` lets it record what it worked on.

    ``label`` is anonymised too, by the same rules as ``text``: it usually
    is the uploaded file name, and those carry the applicant's name far more
    often than the CV's body does. Anything an extension has to show back to
    its owner (a real name, an e-mail, a home town) comes from the account
    profile, never from what crosses this port.

    Called inside the request (and, on PostgreSQL, inside its transaction):
    return promptly — enqueue the work, start it from ``rls.on_commit`` —
    never call a model or a network service inline.
    """

    def analyze_cv(
        self, owner, *, document_id: int, label: str, language: str, text: AnonymizedText
    ) -> None: ...

