"""File storage adapters on Django's own backends: disk and memory.

Each adapter is two things at once. A Django ``Storage`` — the one
``settings.STORAGES["default"]`` names, so ``Document.file`` keeps saving,
opening and deleting through it, ``import_legacy`` and the extensions keep
calling ``document.file.save(...)``, and no migration is needed. And an
implementation of ``tracker.ports.StoragePort`` — what the use cases and
the views are written against. One object, one configuration
(``jobhunt.storage``), whatever the provider.

The provider-specific part is small: how the bytes are stored, and what a
*secure link* is. Here a link is the URL of ``tracker:private_file`` with a
signed token naming the file and the account found in its path
(``tracker.links``); the view checks the signature and the age, then asks
the persistence port whether the signed-in account really owns a document
with that file — the link is evidence, the row is the authority — and only
then streams it. Nothing in ``MEDIA_ROOT`` is ever mounted by URL, so that
view and the pk-based download view, which streams through the same port,
are the only ways out. The Azure adapter
(``tracker.adapters.azure_storage``) answers with a SAS URL instead, which
the browser follows to the blob itself.

``document.file.url`` is the same link: a template or the admin printing it
shows a five-minute link the owner alone can open, never a ``/media/…``
path.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import IO

from django.core.exceptions import SuspiciousFileOperation
from django.core.files.base import ContentFile, File
from django.core.files.storage import FileSystemStorage, InMemoryStorage, Storage

from tracker import privacy
from tracker.adapters import document_text
from tracker.links import DEFAULT_LINK_TTL, make_link
from tracker.ports import MissingFile
from tracker.privacy import AnonymizedText

logger = logging.getLogger(__name__)


@contextmanager
def _reading(file_name: str):
    """A name no storage would accept reads like a name nothing is stored
    under: the caller gets ``MissingFile`` (a 404) rather than Django's
    ``SuspiciousFileOperation`` (a 400 and a mail to the admins)."""
    try:
        yield
    except FileNotFoundError as exc:
        raise MissingFile(file_name) from exc
    except SuspiciousFileOperation as exc:
        logger.warning("Nom de fichier refusé par le stockage : %s (%s)", file_name, exc)
        raise MissingFile(file_name) from exc


class StoragePortMixin(Storage):
    """The port's operations, written once with Django's storage primitives.

    A ``Storage`` for the type checker's sake: the concrete adapter brings
    the real backend (``FileSystemStorage``, ``InMemoryStorage``, or the
    Azure one) and this class only adds the port's vocabulary on top of
    ``save``/``open``/``delete``/``exists``.
    """

    link_ttl: int = DEFAULT_LINK_TTL

    @property
    def served_by_app(self) -> bool:
        """The application streams the bytes itself (see ``StoragePort``)."""
        return True

    def save_file(
        self, file_name: str, content: bytes | IO[bytes] | File, *, max_length: int | None = None
    ) -> str:
        if isinstance(content, (bytes, bytearray)):
            content = ContentFile(bytes(content))
        elif not isinstance(content, File):
            content = File(content)
        # ``generate_filename`` sanitises the basename and refuses ``..``;
        # ``save`` suffixes the name while it is taken, truncating what it
        # must so the stored name fits the caller's column.
        name = self.generate_filename(file_name)
        return self.save(name, content, max_length=max_length)

    def open_file(self, file_name: str) -> File:
        with _reading(file_name):
            return self.open(file_name, "rb")

    def delete_file(self, file_name: str) -> None:
        try:
            with _reading(file_name):
                self.delete(file_name)
        except MissingFile:
            pass

    def file_exists(self, file_name: str) -> bool:
        try:
            with _reading(file_name):
                return self.exists(file_name)
        except MissingFile:
            return False

    def file_size(self, file_name: str) -> int:
        with _reading(file_name):
            return self.size(file_name)

    def file_modified_at(self, file_name: str) -> dt.datetime:
        with _reading(file_name):
            return self.get_modified_time(file_name)

    def list_files(self, folder: str = "") -> Iterator[str]:
        stack = [folder.strip("/")]
        while stack:
            current = stack.pop()
            try:
                with _reading(current):
                    directories, files = self.listdir(current)
            except (MissingFile, NotImplementedError):
                continue
            for name in files:
                yield f"{current}/{name}" if current else name
            stack.extend(f"{current}/{name}" if current else name for name in directories)

    def extract_and_anonymize_text(
        self, file_name: str, *, known: Mapping[str, str] | None = None
    ) -> AnonymizedText:
        with self.open_file(file_name) as handle:
            with _reading(file_name):
                data = handle.read()
        text = document_text.extract_text(file_name, data)
        return privacy.anonymize(text, known=known)


class SignedLinkMixin(StoragePortMixin):
    """Links served by the application itself: ``tracker:private_file``.

    Minting one proves nothing about the asker; the view checks the rows.
    """

    def get_secure_url(self, file_name: str) -> str:
        return make_link(file_name)

    def url(self, name: str | None) -> str:
        if not name:
            raise ValueError("This file is not accessible via a URL.")
        return make_link(name)


class LocalStorageAdapter(SignedLinkMixin, FileSystemStorage):
    """Files under ``MEDIA_ROOT`` — a private directory, never mounted by URL.

    ``location`` is left to ``settings.MEDIA_ROOT`` on purpose: the tests
    override it per class, and the storage follows.
    """

    def __init__(self, *, link_ttl: int = DEFAULT_LINK_TTL, location=None) -> None:
        super().__init__(location=location, base_url=None)
        self.link_ttl = link_ttl


class MemoryStorageAdapter(SignedLinkMixin, InMemoryStorage):
    """Files kept in memory: the DB-free tests, and the ``memory`` provider.

    One store per instance, nothing shared — like ``MemoryPersistence``.
    """

    def __init__(self, *, link_ttl: int = DEFAULT_LINK_TTL, location=None) -> None:
        super().__init__(location=location, base_url=None)
        self.link_ttl = link_ttl
