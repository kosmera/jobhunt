"""Where uploaded files live, as configuration: one variable, one ``STORAGES`` entry.

The counterpart of :mod:`jobhunt.database` for files. Locally they sit in
``MEDIA_ROOT``; on Azure they go to a Blob Storage container. Which one is
``JOBHUNT_STORAGE_PROVIDER``'s call, never the application code's: every
adapter speaks the same port (``tracker.ports.StoragePort``) and doubles as
the Django storage backend behind ``Document.file``, so the same object
serves the model field, the download view and the use cases.

Pure: nothing here reads Django settings, imports the Azure SDK or consults
the process environment except through the arguments, and every call returns
a fresh dict.

Providers::

    local    MEDIA_ROOT on disk, links served by a signed, login-protected view
    azure    Azure Blob Storage, links are 5-minute read-only SAS URLs
    memory   files kept in memory (tests, demos) — lost when the process stops

Azure needs either ``JOBHUNT_AZURE_STORAGE_CONNECTION_STRING`` (account key
inside) or ``JOBHUNT_AZURE_STORAGE_ACCOUNT_URL`` — with
``JOBHUNT_AZURE_STORAGE_ACCOUNT_KEY``, or without it to sign in with the
identity of the process (managed identity, ``azure-identity``). A connection
string is *parsed here* into an endpoint and a key: what reaches
``settings.STORAGES`` is an ``account_key``, which Django's error reporter
redacts, and never the whole string, which it would print in full.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

PROVIDER_ENV = "JOBHUNT_STORAGE_PROVIDER"
LINK_TTL_ENV = "JOBHUNT_STORAGE_LINK_TTL"
CONNECTION_STRING_ENV = "JOBHUNT_AZURE_STORAGE_CONNECTION_STRING"
ACCOUNT_URL_ENV = "JOBHUNT_AZURE_STORAGE_ACCOUNT_URL"
ACCOUNT_KEY_ENV = "JOBHUNT_AZURE_STORAGE_ACCOUNT_KEY"
CONTAINER_ENV = "JOBHUNT_AZURE_STORAGE_CONTAINER"
RETRY_TOTAL_ENV = "JOBHUNT_AZURE_STORAGE_RETRY_TOTAL"
CONNECTION_TIMEOUT_ENV = "JOBHUNT_AZURE_STORAGE_CONNECTION_TIMEOUT"
READ_TIMEOUT_ENV = "JOBHUNT_AZURE_STORAGE_READ_TIMEOUT"
INITIAL_BACKOFF_ENV = "JOBHUNT_AZURE_STORAGE_INITIAL_BACKOFF"

LOCAL = "local"
AZURE = "azure"
MEMORY = "memory"
PROVIDERS = (LOCAL, AZURE, MEMORY)

BACKENDS = {
    LOCAL: "tracker.adapters.file_storage.LocalStorageAdapter",
    AZURE: "tracker.adapters.azure_storage.AzureStorageAdapter",
    MEMORY: "tracker.adapters.file_storage.MemoryStorageAdapter",
}

#: How long a link handed out by ``get_secure_url`` stays valid, in seconds.
DEFAULT_LINK_TTL = 300
#: An hour at most: a longer-lived link is a public file with extra steps,
#: and an Azure SAS cannot be revoked before it expires.
MAX_LINK_TTL = 3600
DEFAULT_CONTAINER = "documents"

#: The SDK's own defaults bound a failed call at about a minute (three tries,
#: 15 s + 18 s + 24 s of backoff). A page waiting on a file cannot: one retry,
#: a short connection timeout and a one-second backoff fail in a few seconds.
AZURE_DEFAULTS = {
    "retry_total": 1,
    "connection_timeout": 3,
    "read_timeout": 30,
    "initial_backoff": 1,
}

# Azure container naming: 3–63 chars, lower-case letters, digits and single dashes.
_CONTAINER_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){2,62}$")

#: Hosts where plain HTTP means the emulator, not a mistake.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Azurite, the local emulator: a fixed endpoint and a documented key.
AZURITE_ACCOUNT = "devstoreaccount1"
AZURITE_URL = f"http://127.0.0.1:10000/{AZURITE_ACCOUNT}"
AZURITE_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
)


def _positive_int(name: str, raw: str, default: int, *, maximum: int) -> int:
    raw = raw.strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if not 0 <= value <= maximum:
        raise ImproperlyConfigured(
            f"{name} vaut « {raw} » ; attendu : un nombre entre 0 et {maximum}."
        )
    return value


def link_ttl_from_env(environ: Mapping[str, str] | None = None) -> int:
    """Seconds of validity of a secure link, from ``JOBHUNT_STORAGE_LINK_TTL``."""
    raw = (os.environ if environ is None else environ).get(LINK_TTL_ENV, "").strip()
    if not raw:
        return DEFAULT_LINK_TTL
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if not 1 <= value <= MAX_LINK_TTL:
        raise ImproperlyConfigured(
            f"{LINK_TTL_ENV} vaut « {raw} » ; attendu : un nombre de secondes entre 1 et {MAX_LINK_TTL}."
        )
    return value


def storage_config(provider: str, environ: Mapping[str, str] | None = None) -> dict:
    """The ``STORAGES["default"]`` entry for ``provider``; fresh dict each call."""
    environ = os.environ if environ is None else environ
    provider = (provider or LOCAL).strip().lower()
    if provider not in BACKENDS:
        raise ImproperlyConfigured(
            f"{PROVIDER_ENV} vaut « {provider} » ; attendu : {', '.join(PROVIDERS)}."
        )
    options: dict[str, object] = {"link_ttl": link_ttl_from_env(environ)}
    if provider == AZURE:
        options.update(_azure_options(environ))
    return {"BACKEND": BACKENDS[provider], "OPTIONS": options}


def parse_connection_string(value: str) -> tuple[str, str]:
    """``(account_url, account_key)`` out of an Azure connection string.

    Only the shapes that let the application *sign links* are accepted: a
    string carrying a shared access signature instead of an account key
    cannot mint one, and is refused here rather than at the first download.
    """
    parts: dict[str, str] = {}
    for chunk in value.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        key, separator, item = chunk.partition("=")
        if not separator:
            raise ImproperlyConfigured(
                f"{CONNECTION_STRING_ENV} : « {chunk} » n'est pas une paire clé=valeur."
            )
        parts[key.strip().lower()] = item.strip()

    if parts.get("usedevelopmentstorage", "").lower() == "true":
        return AZURITE_URL, AZURITE_KEY
    if "sharedaccesssignature" in parts:
        raise ImproperlyConfigured(
            f"{CONNECTION_STRING_ENV} contient une signature d'accès (SharedAccessSignature) "
            "et non une clé de compte : impossible de signer des liens. Donne la clé du "
            f"compte, ou {ACCOUNT_URL_ENV} avec une identité managée."
        )

    account = parts.get("accountname", "")
    key = parts.get("accountkey", "")
    endpoint = parts.get("blobendpoint", "").rstrip("/")
    if not key or not (endpoint or account):
        raise ImproperlyConfigured(
            f"{CONNECTION_STRING_ENV} : il y manque AccountName/AccountKey "
            "(ou BlobEndpoint et AccountKey)."
        )
    if not endpoint:
        scheme = parts.get("defaultendpointsprotocol", "https")
        suffix = parts.get("endpointsuffix", "core.windows.net")
        endpoint = f"{scheme}://{account}.blob.{suffix}"
    return endpoint, key


def _azure_options(environ: Mapping[str, str]) -> dict[str, object]:
    connection_string = environ.get(CONNECTION_STRING_ENV, "").strip()
    account_url = environ.get(ACCOUNT_URL_ENV, "").strip()
    account_key = environ.get(ACCOUNT_KEY_ENV, "").strip()
    container = environ.get(CONTAINER_ENV, "").strip() or DEFAULT_CONTAINER

    if bool(connection_string) == bool(account_url):
        raise ImproperlyConfigured(
            f"{PROVIDER_ENV}=azure : pose soit {CONNECTION_STRING_ENV}, soit "
            f"{ACCOUNT_URL_ENV} (avec {ACCOUNT_KEY_ENV}, ou sans pour une identité managée)."
        )
    if connection_string:
        if account_key:
            raise ImproperlyConfigured(
                f"{ACCOUNT_KEY_ENV} est inutile avec {CONNECTION_STRING_ENV} (la clé y est déjà)."
            )
        # Parsed here so the string itself never reaches the settings: Django
        # redacts a value named « key », not one named « connection_string ».
        account_url, account_key = parse_connection_string(connection_string)
        host = urlsplit(account_url).hostname or ""
        if urlsplit(account_url).scheme != "https" and host not in LOOPBACK_HOSTS:
            # Otherwise the SAS is minted without ``spr=https`` and the CV,
            # token included, travels in clear.
            raise ImproperlyConfigured(
                f"{CONNECTION_STRING_ENV} : l'endpoint doit être en https (« {account_url} ») ; "
                "http n'est admis que sur la boucle locale, pour l'émulateur Azurite."
            )
    else:
        if not account_url.lower().startswith("https://"):
            raise ImproperlyConfigured(
                f"{ACCOUNT_URL_ENV} doit commencer par https:// (« {account_url} »)."
            )
        if "?" in account_url:
            raise ImproperlyConfigured(
                f"{ACCOUNT_URL_ENV} ne doit pas porter de paramètres (« {account_url} »)."
            )
        account_url = account_url.rstrip("/")

    if not _CONTAINER_NAME.match(container):
        raise ImproperlyConfigured(
            f"{CONTAINER_ENV} vaut « {container} » ; attendu : 3 à 63 caractères, "
            "minuscules, chiffres et tirets simples."
        )
    return {
        "account_url": account_url,
        "account_key": account_key or None,
        "container": container,
        "retry_total": _positive_int(
            RETRY_TOTAL_ENV, environ.get(RETRY_TOTAL_ENV, ""), AZURE_DEFAULTS["retry_total"], maximum=10
        ),
        "connection_timeout": _positive_int(
            CONNECTION_TIMEOUT_ENV,
            environ.get(CONNECTION_TIMEOUT_ENV, ""),
            AZURE_DEFAULTS["connection_timeout"],
            maximum=300,
        ),
        "read_timeout": _positive_int(
            READ_TIMEOUT_ENV, environ.get(READ_TIMEOUT_ENV, ""), AZURE_DEFAULTS["read_timeout"], maximum=600
        ),
        "initial_backoff": _positive_int(
            INITIAL_BACKOFF_ENV,
            environ.get(INITIAL_BACKOFF_ENV, ""),
            AZURE_DEFAULTS["initial_backoff"],
            maximum=60,
        ),
    }
