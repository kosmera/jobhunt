"""Configuration checks, run with every management command.

Registered under the plain ``tracker`` tag rather than ``Tags.database`` on
purpose: ``runserver`` skips database-tagged checks, and this one is precisely
the warning a developer should see before serving.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Warning, register

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
