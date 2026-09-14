"""Configuration checks, run with every management command."""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, Warning, register

from accounts import conf


@register("accounts")
def check_email_delivery(app_configs, **kwargs):
    queue = settings.Q_CLUSTER
    problems = []
    if queue.get("orm") != "default" or queue.get("sync", False):
        problems.append(Error(
            "Les e-mails nécessitent la file Q2 ORM sur default, avec sync=False.", id="accounts.E002",
        ))
    if settings.JOBHUNT_EMAIL_JOB_TIMEOUT <= 0 or settings.JOBHUNT_EMAIL_MAX_ATTEMPTS <= 0:
        problems.append(Error("Les délais et le nombre d'essais e-mail doivent être positifs.", id="accounts.E003"))
    list_id = settings.JOBHUNT_BREVO_LAUNCH_LIST_ID
    if list_id is not None and (isinstance(list_id, bool) or not isinstance(list_id, int) or list_id <= 0):
        problems.append(Error("JOBHUNT_BREVO_LAUNCH_LIST_ID doit être un identifiant de liste positif.", id="accounts.E004"))
    try:
        public = urlsplit(settings.JOBHUNT_PUBLIC_URL)
        valid_url = public.scheme in {"https", "http"} and public.hostname and not (
            public.username or public.password or public.query or public.fragment
        )
    except ValueError:
        valid_url = False
    if not valid_url:
        problems.append(Error("JOBHUNT_PUBLIC_URL doit être l'URL publique HTTP(S) du site.", id="accounts.E005"))
    return problems


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
