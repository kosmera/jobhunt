"""Configuration checks, run with every management command."""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, Warning, register

from accounts import conf


@register("accounts")
def check_auth_mode(app_configs, **kwargs):
    problems = []
    if settings.AUTH_MODE not in {conf.LOCAL, conf.ACCOUNTS}:
        problems.append(
            Error(
                f"JOBHUNT_AUTH_MODE vaut « {settings.AUTH_MODE} » ; attendu : local ou accounts.",
                id="accounts.E001",
            )
        )
    elif not conf.is_local() and settings.SECRET_KEY.startswith("django-insecure-"):
        problems.append(
            Warning(
                "Mode comptes avec la clé secrète par défaut : pose JOBHUNT_SECRET_KEY "
                "avant d'exposer l'application.",
                id="accounts.W001",
            )
        )
    return problems
