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


@register("accounts")
def check_tls_origin(app_configs, **kwargs):
    """Behind TLS, Django only trusts an https origin it was told about."""
    if (
        not conf.is_local()
        and not settings.DEBUG
        and not settings.SECURE_PROXY_SSL_HEADER
        and not any(origin.startswith("https://") for origin in settings.CSRF_TRUSTED_ORIGINS)
    ):
        return [
            Warning(
                "Mode comptes sans origine https dans JOBHUNT_CSRF_TRUSTED_ORIGINS ni "
                "SECURE_PROXY_SSL_HEADER : derrière TLS, chaque formulaire (et chaque action "
                "HTMX) sera refusé pour CSRF.",
                id="accounts.W002",
            )
        ]
    return []
