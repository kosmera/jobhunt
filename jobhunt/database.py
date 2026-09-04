"""The database engine as configuration: one URL in, a ``DATABASES`` entry out.

Local development runs on SQLite, production on a managed PostgreSQL (Azure).
The ORM already hides the engine from the application code; what is left is
choosing it without editing ``settings.py``, which is what
``JOBHUNT_DATABASE_URL`` does. Everything here is pure: no Django settings are
read, no environment is consulted except through the small helpers built for
it, and every call returns a fresh dict because Django mutates ``OPTIONS`` in
place once the connection is configured.

Supported spellings::

    sqlite:///db.sqlite3            relative to the project directory
    sqlite:////var/lib/jobhunt/db   absolute path
    sqlite:///:memory:              in-memory database
    postgres://user:pwd@host:5432/name?sslmode=require&pool=1
    postgresql://…                  same thing

A relative SQLite path is anchored on ``base_dir`` rather than on the current
directory on purpose: a service unit started from ``/`` would otherwise open a
brand-new empty database, and ``AUTO_MIGRATE`` would make that look normal.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

SQLITE_ENGINE = "django.db.backends.sqlite3"
# Django's PostgreSQL backend plus the row-level-security hook: every
# outermost transaction announces the bound account (``rls.backends``).
POSTGRESQL_ENGINE = "rls.backends.postgresql"

# What today's settings hand to SQLite: writers do not wait on readers (WAL)
# and a write transaction takes its lock up front instead of failing later.
SQLITE_OPTIONS = {"transaction_mode": "IMMEDIATE", "init_command": "PRAGMA journal_mode=WAL;"}

# Hosts that mean "this machine": no TLS by default, and a DEBUG checkout may
# keep migrating on its own.
LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})

CONN_MAX_AGE_ENV = "JOBHUNT_DB_CONN_MAX_AGE"
DEFAULT_CONN_MAX_AGE = 60

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def is_local_host(host: str | None) -> bool:
    """``True`` for an empty host (Unix socket), localhost, 127.0.0.1 and ::1."""
    host = (host or "").strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host in LOCAL_HOSTS


def auto_migrate_default(debug: bool, host: str | None) -> bool:
    """Whether ``runserver`` should generate and apply migrations by itself.

    Only in development *and* against a local database: a DEBUG checkout
    pointed at the production server must not write to its schema on every
    reload.
    """
    return bool(debug) and is_local_host(host)


def conn_max_age_from_env(
    default: int | None = DEFAULT_CONN_MAX_AGE, environ: os._Environ[str] | dict[str, str] | None = None
) -> int | None:
    """Persistent-connection lifetime in seconds, from ``JOBHUNT_DB_CONN_MAX_AGE``.

    ``0`` closes the connection after each request, a number keeps it that
    long, ``none`` keeps it forever (Django's ``None``). Anything else is a
    configuration error rather than a silent fallback.
    """
    raw = (os.environ if environ is None else environ).get(CONN_MAX_AGE_ENV, "").strip()
    if not raw:
        return default
    if raw.lower() == "none":
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ImproperlyConfigured(
            f"{CONN_MAX_AGE_ENV} vaut « {raw} » ; attendu : un nombre de secondes, 0 ou none."
        ) from None
    if value < 0:
        raise ImproperlyConfigured(
            f"{CONN_MAX_AGE_ENV} vaut « {raw} » ; attendu : un nombre de secondes, 0 ou none."
        )
    return value


def database_config(
    url: str, *, base_dir: Path, conn_max_age: int | None = DEFAULT_CONN_MAX_AGE
) -> dict:
    """Build the ``DATABASES["default"]`` entry described by ``url``.

    Returns a fresh dict on every call. ``conn_max_age`` only applies to
    PostgreSQL — SQLite connections are cheap and per-thread anyway — and is
    forced to ``0`` when pooling is requested, because Django refuses to
    combine a pool with persistent connections.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme == "sqlite":
        return _sqlite_config(url, base_dir)
    if scheme in {"postgres", "postgresql"}:
        return _postgresql_config(url, conn_max_age)
    shown = parts.scheme or url
    raise ImproperlyConfigured(
        f"JOBHUNT_DATABASE_URL : schéma « {shown} » inconnu ; "
        "attendu sqlite://, postgres:// ou postgresql://."
    )


def _sqlite_config(url: str, base_dir: Path) -> dict:
    # Split by hand rather than through ``urlsplit().port``: ``sqlite://:memory:``
    # puts ``:memory:`` in the netloc and makes ``.port`` raise.
    rest = url[len("sqlite:") :]
    if rest in {"//:memory:", "///:memory:"}:
        name: str = ":memory:"
    elif rest.startswith("//"):
        path = rest[2:]
        if not path.startswith("/"):
            raise ImproperlyConfigured(
                f"JOBHUNT_DATABASE_URL : « {url} » — écris sqlite:///chemin/relatif, "
                "sqlite:////chemin/absolu ou sqlite:///:memory:."
            )
        # Exactly one leading slash belongs to the URL syntax; a second one
        # makes the path absolute.
        path = path[1:]
        if not path:
            raise ImproperlyConfigured(
                f"JOBHUNT_DATABASE_URL : « {url} » — le chemin du fichier SQLite manque."
            )
        name = str(Path(base_dir) / path)
    else:
        raise ImproperlyConfigured(
            f"JOBHUNT_DATABASE_URL : « {url} » — écris sqlite:///chemin/relatif, "
            "sqlite:////chemin/absolu ou sqlite:///:memory:."
        )
    return {
        "ENGINE": SQLITE_ENGINE,
        "NAME": name,
        "OPTIONS": dict(SQLITE_OPTIONS),
        "CONN_MAX_AGE": 0,
    }


def _postgresql_config(url: str, conn_max_age: int | None) -> dict:
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        raise ImproperlyConfigured(
            f"JOBHUNT_DATABASE_URL : port invalide dans « {parts.netloc} »."
        ) from None
    host = parts.hostname or ""

    options: dict[str, object] = {}
    # ``parse_qs`` keeps every repetition; the last one wins, blanks are
    # dropped so ``?sslmode=`` means "not given" rather than "empty string".
    for key, values in parse_qs(parts.query, keep_blank_values=True).items():
        value = values[-1]
        if value == "":
            continue
        options[key] = value

    pool = False
    if "pool" in options:
        raw_pool = str(options.pop("pool")).lower()
        if raw_pool in _TRUE:
            pool = True
        elif raw_pool not in _FALSE:
            raise ImproperlyConfigured(
                f"JOBHUNT_DATABASE_URL : pool vaut « {raw_pool} » ; attendu : 1/0, true/false, yes/no, on/off."
            )
    if pool:
        options["pool"] = True
        # Django raises "Pooling doesn't support persistent connections" for
        # any non-zero CONN_MAX_AGE (django/db/backends/postgresql/base.py).
        conn_max_age = 0

    # A startup option pre-setting the account (``options=-c app.current_user_id=…``)
    # would make every anonymous request act for it: refused outright.
    if "app.current_user_id" in str(options.get("options", "")):
        raise ImproperlyConfigured(
            "JOBHUNT_DATABASE_URL : « options » ne doit pas fixer app.current_user_id."
        )
    # Azure enforces TLS; a remote host without an explicit choice gets it.
    if not is_local_host(host):
        options.setdefault("sslmode", "require")
    options.setdefault("application_name", "jobhunt")

    return {
        "ENGINE": POSTGRESQL_ENGINE,
        "NAME": unquote(parts.path[1:]) if parts.path.startswith("/") else unquote(parts.path),
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": host,
        "PORT": str(port) if port is not None else "",
        "CONN_MAX_AGE": conn_max_age,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": options,
    }
