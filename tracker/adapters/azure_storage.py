"""Files on Azure Blob Storage, links as short-lived SAS URLs.

The same object as ``tracker.adapters.file_storage.LocalStorageAdapter`` seen
from the application — a Django ``Storage`` that speaks
``tracker.ports.StoragePort`` — with the Azure SDK behind it. The SDK is
imported on first use, so the module loads without ``azure-storage-blob``
and the configuration check can say what is missing.

What is specific here:

- a blob is written once (``overwrite=False``; the name is taken by
  ``Storage.get_available_name`` first, and a race on the same name is
  retried under another one). The stream is rewound before each attempt:
  the SDK reads from the current position and never seeks, so a partly
  consumed upload would be stored truncated;
- a download is one request for anything the application accepts (the
  SDK fetches up to 32 MiB in the first call); the handle it returns has
  ``read``, ``readall``, ``size`` and ``close`` — enough for
  ``FileResponse`` and for text extraction, no more — and reopening it
  downloads again rather than seeking;
- ``get_secure_url`` signs a read-only, HTTPS-only SAS with the account key,
  or with a *user delegation key* when the process signs in with an identity
  (``azure-identity``); that key is fetched for two hours and refreshed
  under a lock before it could stop covering a link;
- every SDK failure becomes ``StorageError`` (an ``OSError``), a missing
  blob ``MissingFile``, so a provider outage degrades the page instead of
  breaking it. The client is built with one retry and short timeouts for the
  same reason: the SDK's own defaults make a failed call take about a minute.

Configuration: ``jobhunt.storage`` (``JOBHUNT_AZURE_STORAGE_*``). Identity
sign-in needs the roles *Storage Blob Data Contributor* (read, write,
delete) and *Storage Blob Delegator* (the delegation key) on the account.
"""

from __future__ import annotations

import datetime as dt
import mimetypes
import posixpath
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cached_property
from typing import IO, Any, cast
from urllib.parse import urlsplit, urlunsplit

from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import File
from django.utils.http import content_disposition_header

from jobhunt.storage import LOOPBACK_HOSTS
from tracker.adapters.file_storage import StoragePortMixin
from tracker.links import DEFAULT_LINK_TTL
from tracker.ports import MissingFile, StorageError

DEFAULT_CONTAINER = "documents"

#: A link starts a little in the past: the clocks of Azure and of this
#: server need not agree to the second.
CLOCK_SKEW = dt.timedelta(minutes=5)
#: A user delegation key is asked for this long, and renewed while at least
#: ``link_ttl`` plus this margin remain — a link must never outlive the key
#: that signed it.
DELEGATION_KEY_LIFETIME = dt.timedelta(hours=2)
DELEGATION_KEY_MARGIN = dt.timedelta(minutes=15)

INSTALL_HINT = "installe le pilote : uv sync --extra azure (ou uv pip install azure-storage-blob)."


def _sdk():
    """The SDK names this module uses, imported on first use."""
    try:
        import azure.storage.blob as blob
        from azure.core import exceptions
    except ImportError as exc:
        raise ImproperlyConfigured(f"Stockage Azure : {INSTALL_HINT} ({exc})") from exc
    return blob, exceptions


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@contextmanager
def _translating(name: str):
    """Every SDK failure inside the block as the port's own exception.

    ``ResourceNotFoundError`` is a missing *blob* — unless the container
    itself is missing, which is a deployment problem and not something a
    page should render as "file gone". Everything else in the SDK's
    hierarchy (a refused connection, a timeout, expired credentials, a
    stream cut mid-download) is a provider failure. The one exception is
    ``ResourceExistsError``, which ``_save`` handles as a name collision.
    """
    _, exceptions = _sdk()
    try:
        yield
    except exceptions.ResourceExistsError:
        # Not a failure: the caller retries under another name.
        raise
    except exceptions.ResourceNotFoundError as exc:
        if str(getattr(exc, "error_code", "")) == "ContainerNotFound":
            raise StorageError(f"Stockage Azure : conteneur introuvable ({name}).") from exc
        raise MissingFile(name) from exc
    except exceptions.AzureError as exc:
        raise StorageError(
            f"Stockage Azure : {name} — {exc.__class__.__name__}: {exc}"
        ) from exc


class _BlobReader:
    """What ``download_blob()`` returns, as a minimal binary file-like.

    No ``seek`` and no ``tell`` on purpose: ``FileResponse`` probes for them
    to compute a ``Content-Length``, and a blob stream has no position to go
    back to — the view reads ``size`` instead.
    """

    def __init__(self, blob: Any, name: str) -> None:
        self._blob = blob
        self.name = name
        self.closed = False
        with _translating(name):
            self._downloader = blob.download_blob()
        self.size: int = self._downloader.size

    def open(self, mode: str = "rb") -> _BlobReader:
        if mode not in ("rb", "r"):
            raise ValueError("Un blob se lit ; il ne s'ouvre pas en écriture.")
        with _translating(self.name):
            self._downloader = self._blob.download_blob()
        self.closed = False
        return self

    def read(self, size: int = -1) -> bytes:
        # A stream can fail long after the first request: keep translating.
        with _translating(self.name):
            return self._downloader.read(size)

    def readall(self) -> bytes:
        with _translating(self.name):
            return self._downloader.readall()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def close(self) -> None:
        self.closed = True


class _BlobFile(File):
    """``File`` over a :class:`_BlobReader`: reopening downloads again rather
    than seeking."""

    def open(self, mode: str | None = None, *args, **kwargs) -> _BlobFile:
        cast(_BlobReader, self.file).open(mode or "rb")
        return self


class AzureStorageAdapter(StoragePortMixin):
    def __init__(
        self,
        *,
        container: str = DEFAULT_CONTAINER,
        account_url: str | None = None,
        account_key: str | None = None,
        credential: Any = None,
        link_ttl: int = DEFAULT_LINK_TTL,
        client: Any = None,
        retry_total: int = 1,
        connection_timeout: int = 3,
        read_timeout: int = 30,
        initial_backoff: int = 1,
    ) -> None:
        if client is None and not account_url:
            raise ImproperlyConfigured(
                "Stockage Azure : donne l'URL du compte (voir jobhunt/storage.py)."
            )
        self.container_name = container
        self.link_ttl = link_ttl
        self._account_url = account_url
        self._account_key = account_key
        self._credential = credential
        self._injected_client = client
        self._client_options = {
            "retry_total": retry_total,
            "connection_timeout": connection_timeout,
            "read_timeout": read_timeout,
            # The SDK's exponential backoff sleeps 15, 18 then 24 seconds by
            # default; a request a page is waiting on cannot afford that.
            "initial_backoff": initial_backoff,
            "increment_base": 2,
            "random_jitter_range": 1,
        }
        self._delegation_lock = threading.Lock()
        self._delegation_key: tuple[Any, dt.datetime] | None = None

    # -- clients ------------------------------------------------------------

    @cached_property
    def client(self) -> Any:
        """The ``BlobServiceClient`` — injected (tests), or built from the settings."""
        if self._injected_client is not None:
            return self._injected_client
        blob, _ = _sdk()
        credential = self._account_key or self._credential
        if credential is None:
            try:
                from azure.identity import DefaultAzureCredential
            except ImportError as exc:
                raise ImproperlyConfigured(
                    "Stockage Azure sans clé de compte : l'identité du processus demande "
                    f"azure-identity ({exc})."
                ) from exc
            credential = DefaultAzureCredential()
        return blob.BlobServiceClient(
            self._account_url, credential=credential, **self._client_options
        )

    @cached_property
    def container(self) -> Any:
        return self.client.get_container_client(self.container_name)

    @cached_property
    def signing(self) -> tuple[str, str]:
        """How links get signed: ``("key", account name)`` or ``("identity", …)``.

        Settled once, at the first link, rather than at every download: a
        client that can read and write blobs but cannot sign a link (a
        connection string carrying a SAS, an anonymous client) is a
        configuration mistake, and it should say so.
        """
        credential = getattr(self.client, "credential", None)
        account_name = getattr(self.client, "account_name", None) or ""
        if getattr(credential, "account_key", None):
            return "key", getattr(credential, "account_name", None) or account_name
        if hasattr(credential, "get_token"):
            return "identity", account_name
        raise ImproperlyConfigured(
            "Stockage Azure : cette configuration ne permet pas de signer des liens "
            "(pas de clé de compte ni d'identité). Vois « Stockage des fichiers » dans le README."
        )

    def _blob(self, name: str) -> Any:
        return self.container.get_blob_client(name)

    # -- Django storage ---------------------------------------------------

    @property
    def served_by_app(self) -> bool:
        return False

    def _open(self, name: str, mode: str = "rb") -> File:
        if mode not in ("rb", "r"):
            raise ValueError("Un blob se lit ; il ne s'ouvre pas en écriture.")
        # A binary file-like in every way ``File`` uses; not an ``IO`` for the checker.
        return _BlobFile(cast(IO[bytes], _BlobReader(self._blob(name), name)), name)

    def save(self, name, content, max_length=None):
        """``Storage.save``, retried once on a name taken between check and write.

        The retry has to happen here rather than in ``_save``: ``Storage.save``
        does not pass ``max_length`` down, and picking a replacement name
        without it would return something wider than the column that has to
        hold it. Going through ``super().save`` again re-runs
        ``get_available_name``, which trims.
        """
        _, exceptions = _sdk()
        for attempt in range(2):
            try:
                return super().save(name, content, max_length=max_length)
            except exceptions.ResourceExistsError:
                if attempt:
                    raise StorageError(f"Stockage Azure : {name} existe déjà.") from None
        raise AssertionError  # pragma: no cover — the loop returns or raises

    def _save(self, name: str, content: Any) -> str:
        blob, _ = _sdk()
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if hasattr(content, "seek") and content.seekable():
            # The SDK reads from the current position and never rewinds:
            # a stream left mid-way would be stored truncated.
            content.seek(0)
        # ``overwrite=False`` sends If-None-Match; the 409 it may answer is
        # not a failure but a name collision, which ``save`` retries.
        with _translating(name):
            self.container.upload_blob(
                name,
                content,
                length=content.size,
                overwrite=False,
                content_settings=blob.ContentSettings(content_type=content_type),
            )
        return name

    def delete(self, name: str) -> None:
        try:
            with _translating(name):
                self._blob(name).delete_blob()
        except MissingFile:
            pass

    def exists(self, name: str) -> bool:
        with _translating(name):
            return bool(self._blob(name).exists())

    def size(self, name: str) -> int:
        with _translating(name):
            return int(self._blob(name).get_blob_properties().size)

    def url(self, name: str | None) -> str:
        if not name:
            raise ValueError("This file is not accessible via a URL.")
        return self.get_secure_url(name)

    def file_size(self, file_name: str) -> int:
        return self.size(file_name)

    def file_modified_at(self, file_name: str) -> dt.datetime:
        with _translating(file_name):
            return self._blob(file_name).get_blob_properties().last_modified

    def list_files(self, folder: str = "") -> Iterator[str]:
        # A folder, not a bare prefix: « documents » must not bring back
        # « documents-old/… » the way ``name_starts_with`` alone would.
        folder = folder.strip("/")
        with _translating(f"liste {folder}"):
            for blob in self.container.list_blobs(name_starts_with=f"{folder}/" if folder else None):
                yield blob.name

    # -- the port ---------------------------------------------------------

    def get_secure_url(self, file_name: str) -> str:
        """A read-only SAS URL for that blob, valid ``link_ttl`` seconds,
        downloading under the file's own basename."""
        blob, _ = _sdk()
        now = _now()
        kind, account_name = self.signing
        material: dict[str, Any] = {}
        if kind == "key":
            material["account_key"] = self.client.credential.account_key
        else:
            material["user_delegation_key"] = self._user_delegation_key(now)
        if not account_name:
            raise ImproperlyConfigured("Stockage Azure : nom de compte introuvable.")
        blob_client = self._blob(file_name)
        parts = urlsplit(blob_client.url)
        with _translating(file_name):
            sas = blob.generate_blob_sas(
                account_name=account_name,
                container_name=self.container_name,
                blob_name=file_name,
                permission=blob.BlobSasPermissions(read=True),
                start=now - CLOCK_SKEW,
                expiry=now + dt.timedelta(seconds=self.link_ttl),
                content_disposition=content_disposition_header(
                    True, posixpath.basename(file_name)
                ),
                # The service's own default allows plain HTTP. Only the
                # emulator, on the loopback, is left unpinned.
                protocol=None if (parts.hostname or "") in LOOPBACK_HOSTS else "https",
                **material,
            )
        # ``blob.url`` may already carry a query (a client built with a SAS).
        return urlunsplit(parts._replace(query=sas))

    def _user_delegation_key(self, now: dt.datetime) -> Any:
        with self._delegation_lock:
            cached = self._delegation_key
            needed_until = now + dt.timedelta(seconds=self.link_ttl) + DELEGATION_KEY_MARGIN
            if cached is None or cached[1] < needed_until:
                start = now - DELEGATION_KEY_MARGIN
                expiry = now + DELEGATION_KEY_LIFETIME
                with _translating("(clé de délégation)"):
                    key = self.client.get_user_delegation_key(start, expiry)
                cached = self._delegation_key = (key, expiry)
            return cached[0]

    # -- operations -------------------------------------------------------

    def describe(self) -> dict[str, str]:
        """What ``manage.py storage_status`` prints about this adapter."""
        kind, account_name = self.signing
        return {
            "Compte": account_name or "—",
            "Conteneur": self.container_name,
            "Signature": "clé de compte" if kind == "key" else "identité (clé de délégation)",
        }

    def check_container(self) -> None:
        """``StorageError`` unless the container is there and readable.

        ``BlobClient.exists()`` answers ``False`` for a missing container as
        readily as for a missing blob, so a mistyped container name would
        otherwise only surface at the first upload.
        """
        with _translating(f"conteneur {self.container_name}"):
            self.container.get_container_properties()
