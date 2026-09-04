"""Configuration checks, run with every management command.

Registered under the plain ``tracker`` tag rather than ``Tags.database`` on
purpose: ``runserver`` skips database-tagged checks, and this one is precisely
the warning a developer should see before serving.
"""

from __future__ import annotations

from importlib.util import find_spec

from django.conf import settings
from django.core.checks import Error, Warning, register

from accounts import conf


@register("tracker")
def check_database(app_configs, **kwargs):
    problems = []
    engine = settings.DATABASES["default"]["ENGINE"]
    if settings.AUTH_MODE == conf.ACCOUNTS and engine.endswith("sqlite3"):
        problems.append(
            Warning(
                "Mode comptes sur SQLite : une instance partagée mérite PostgreSQL. "
                "Pose JOBHUNT_DATABASE_URL (voir .env.example).",
                id="tracker.W001",
            )
        )
    return problems


@register("tracker")
def check_storage(app_configs, **kwargs):
    """The file provider's driver is installed, and memory is not a mistake.

    No network here: ``manage.py storage_status`` talks to the provider.
    """
    problems = []
    provider = getattr(settings, "STORAGE_PROVIDER", "local")
    options = settings.STORAGES.get("default", {}).get("OPTIONS", {})
    if provider == "azure":
        if find_spec("azure.storage.blob") is None:
            problems.append(
                Error(
                    "Stockage Azure sans pilote : uv sync --extra azure "
                    "(ou uv pip install azure-storage-blob).",
                    id="tracker.E002",
                )
            )
        elif (
            not options.get("connection_string")
            and not options.get("account_key")
            and find_spec("azure.identity") is None
        ):
            problems.append(
                Error(
                    "Stockage Azure par identité managée (pas de clé de compte) : "
                    "azure-identity manque — uv sync --extra azure.",
                    id="tracker.E003",
                )
            )
    elif provider == "memory":
        problems.append(
            Warning(
                "Stockage en mémoire : les fichiers téléversés disparaissent à l'arrêt "
                "du processus. Pose JOBHUNT_STORAGE_PROVIDER=local ou azure.",
                id="tracker.W002",
            )
        )
    return problems

