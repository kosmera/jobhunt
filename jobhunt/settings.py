"""Django settings for the JobHunt application tracker.

Locally hosted by default (one profile, no password), deployable as a shared
instance with accounts — see ``AUTH_MODE`` below. SQLite by default,
PostgreSQL when ``JOBHUNT_DATABASE_URL`` says so — the engine is configuration
(``jobhunt.database``), the application code never chooses it.
"""

import os
from pathlib import Path

from jobhunt.database import auto_migrate_default, conn_max_age_from_env, database_config
from jobhunt.storage import PROVIDER_ENV, storage_config

BASE_DIR = Path(__file__).resolve().parent.parent

# A local tool first. Override via the environment for anything else.
SECRET_KEY = os.environ.get(
    "JOBHUNT_SECRET_KEY", "django-insecure-local-only-jobhunt-tracker-key"
)
DEBUG = os.environ.get("JOBHUNT_DEBUG", "1") == "1"
# Explicit commercial deployment switch, independent of DEBUG and AUTH_MODE.
IS_SAAS_PRODUCTION = os.environ.get("IS_SAAS_PRODUCTION", "false").lower() in {"1", "true"}


def _env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


ALLOWED_HOSTS = _env_list("JOBHUNT_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],0.0.0.0,testserver")
CSRF_TRUSTED_ORIGINS = _env_list(
    "JOBHUNT_CSRF_TRUSTED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
)

# --- Accounts ---------------------------------------------------------------
# ``local``: one machine, no password — the first visit creates the profile,
# a single profile is signed in automatically. ``accounts``: sign-in and
# sign-up forms, for a shared deployment. Local by default in development.
AUTH_MODE = os.environ.get("JOBHUNT_AUTH_MODE", "local" if DEBUG else "accounts")
# Accounts mode only: whether anyone may create an account.
SIGNUP_OPEN = os.environ.get("JOBHUNT_SIGNUP_OPEN", "1") == "1"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "tracker:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"
# Cookies marked Secure are only sent over HTTPS: on by default for a shared
# deployment, off for a local instance served over plain HTTP.
_secure_cookies = os.environ.get(
    "JOBHUNT_SECURE_COOKIES", "1" if AUTH_MODE == "accounts" and not DEBUG else "0"
) == "1"
SESSION_COOKIE_SECURE = _secure_cookies
CSRF_COOKIE_SECURE = _secure_cookies
# Behind a TLS-terminating proxy (Cloudflare, Azure App Service) the request
# reaches Django as plain HTTP: without this it builds ``http://`` URLs and
# thinks a Secure cookie can never be sent. Only safe because the proxy always
# overwrites the header — never set it for a server reachable directly.
if os.environ.get("JOBHUNT_BEHIND_PROXY", "0") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# An HTMX request with a stale token (a sign-in elsewhere rotated it) gets a
# 403 that reloads its page instead of a silent failure.
CSRF_FAILURE_VIEW = "accounts.views.csrf_failure"

INSTALLED_APPS = [
    # Our apps first: Django hands a management command to the first app that
    # defines it, and ``tracker`` overrides ``runserver`` (auto-migration in
    # development), which ``django.contrib.staticfiles`` also overrides.
    "accounts",
    "tracker",
    "rls",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]

# --- Copilote IA ------------------------------------------------------------
# The copilot (``jobhunt_ai``) ships with the core and is on by default. An
# instance that does not want it — no provider key, no worker — turns it off
# here: its app, its URLs (``copilote/``) and its chrome disappear together,
# and ``tracker.adapters.cv_analyzer()`` returns nothing. Python code asks
# ``apps.is_installed("jobhunt_ai")``; templates get ``copilot_enabled``.
COPILOT_ENABLED = os.environ.get("COPILOT_ENABLED", "1").strip().lower() in {"1", "true"}
if COPILOT_ENABLED:
    INSTALLED_APPS += ["django_q", "jobhunt_ai"]

# The copilot reads only namespaced ``JOBHUNT_AI_*`` settings, filled here from
# the environment (``jobhunt_ai/settings.py`` holds the defaults). Legacy
# environment names remain accepted.
# Operator-owned provider credential, unrelated to a user's premium access.
# Optional at startup; only AI execution requires a configured provider. An
# empty value counts as unset, so a blank line in ``.env`` does not hide an
# exported ``ANTHROPIC_API_KEY``.
JOBHUNT_AI_API_KEY = os.environ.get("JOBHUNT_AI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
JOBHUNT_AI_OPENAI_API_KEY = os.environ.get("JOBHUNT_AI_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")

# Deployment knobs, converted from environment strings to their setting type.
for _ai_name in (
    "MODEL", "LOCATION", "BRIGHTDATA_MCP_URL", "AZURE_ENDPOINT", "AZURE_API_KEY",
    "AZURE_API_VERSION", "AZURE_SIMPLE_DEPLOYMENT", "AZURE_COMPLEX_DEPLOYMENT",
    "UPGRADE_URL", "PROVIDER", "OPENAI_SIMPLE_MODEL", "OPENAI_COMPLEX_MODEL",
):
    if f"JOBHUNT_AI_{_ai_name}" in os.environ:
        globals()[f"JOBHUNT_AI_{_ai_name}"] = os.environ[f"JOBHUNT_AI_{_ai_name}"]
for _ai_name in (
    "MAX_TOKENS", "QUEUE_TTL", "WORKER_GRACE", "LLM_MAX_RETRIES",
    "SCRAPE_TIMEOUT", "ANALYZE_TIMEOUT", "RADIUS_KM", "SCOUT_MAX_QUERIES",
    "SCOUT_MAX_PAGES", "SCOUT_PAGE_CHARS",
    "AZURE_MAX_INPUT_CHARS", "AZURE_MAX_OUTPUT_TOKENS",
    "OPENAI_MAX_INPUT_CHARS", "OPENAI_MAX_OUTPUT_TOKENS",
    "FREE_MONTHLY_REQUESTS", "FREE_REQUESTS_PER_MINUTE",
    "PAID_MONTHLY_REQUESTS", "PAID_REQUESTS_PER_MINUTE",
):
    if f"JOBHUNT_AI_{_ai_name}" in os.environ:
        globals()[f"JOBHUNT_AI_{_ai_name}"] = int(os.environ[f"JOBHUNT_AI_{_ai_name}"])
for _ai_name in ("LLM_TIMEOUT", "BRIGHTDATA_TIMEOUT"):
    if f"JOBHUNT_AI_{_ai_name}" in os.environ:
        globals()[f"JOBHUNT_AI_{_ai_name}"] = float(os.environ[f"JOBHUNT_AI_{_ai_name}"])
for _ai_name in ("EAGER", "BRIGHTDATA_FALLBACK"):
    if f"JOBHUNT_AI_{_ai_name}" in os.environ:
        globals()[f"JOBHUNT_AI_{_ai_name}"] = os.environ[f"JOBHUNT_AI_{_ai_name}"] == "1"
JOBHUNT_AI_BRIGHTDATA_API_TOKEN = os.environ.get(
    "JOBHUNT_AI_BRIGHTDATA_API_TOKEN", os.environ.get("BRIGHTDATA_API_TOKEN", "")
)
if os.environ.get("JOBHUNT_AI_SCOUT_SOURCES"):
    import json

    JOBHUNT_AI_SCOUT_SOURCES = json.loads(os.environ["JOBHUNT_AI_SCOUT_SOURCES"])

# Durable tasks: the copilot runs its agents in a django_q cluster (``manage.py
# qcluster``). Keeping the ORM broker on the same database makes job + message
# creation atomic.
_q_workers = int(os.environ.get("JOBHUNT_Q_WORKERS", "2"))
_q_timeout = int(os.environ.get("JOBHUNT_Q_TIMEOUT", "1800"))
Q_CLUSTER = {
    "name": "jobhunt-ai",
    "orm": "default",
    "workers": _q_workers,
    "timeout": _q_timeout,
    # Receipts include time waiting in the worker's local queue. Allow for
    # two waiting batches, not just the currently executing task.
    "retry": int(os.environ.get("JOBHUNT_Q_RETRY", str(3 * _q_timeout + 120))),
    "queue_limit": _q_workers,
    "bulk": 1,
    "poll": 1,
    "recycle": 50,
    "ack_failures": True,
    "max_attempts": 1,
    "save_limit": 250,
    "sync": False,
    "scheduler": False,
}

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Binds every request to the signed-in account before anything reads a
    # protected table (AccountsMiddleware.process_view reads the profile).
    "rls.middleware.RowLevelSecurityMiddleware",
    "accounts.middleware.AccountsMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "jobhunt.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.account",
                "tracker.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "jobhunt.wsgi.application"

# One URL picks the engine: SQLite in the project directory by default,
# PostgreSQL (Azure) in production. Spellings and knobs in jobhunt/database.py
# and .env.example.
DATABASES = {
    "default": database_config(
        os.environ.get("JOBHUNT_DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        base_dir=BASE_DIR,
        conn_max_age=conn_max_age_from_env(),
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fr-be"
TIME_ZONE = "Europe/Brussels"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# --- Files ------------------------------------------------------------------
# Where uploaded documents live is configuration, like the database engine:
# ``local`` keeps them under MEDIA_ROOT (a private directory, never mounted
# by URL), ``azure`` puts them in a Blob Storage container, ``memory`` keeps
# them in the process. The adapter behind ``Document.file`` is also the
# ``StoragePort`` of tracker/ports.py; knobs in jobhunt/storage.py and
# .env.example.
STORAGE_PROVIDER = os.environ.get(PROVIDER_ENV, "local")
STORAGES = {
    "default": storage_config(STORAGE_PROVIDER),
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Deployed, nothing sits in front of gunicorn to serve ``/static/``: whitenoise
# does it, from the directory ``collectstatic`` filled, with hashed names so the
# files can be cached forever. Development needs none of it — ``runserver``
# serves the same files itself — which is why the ``deploy`` extra that provides
# whitenoise is optional and this block is skipped when DEBUG is on.
if not DEBUG:
    # Not the position whitenoise documents (straight after SecurityMiddleware):
    # ``rls.E002`` allows only Django's own middleware before
    # RowLevelSecurityMiddleware, since anything earlier runs its request and
    # response halves outside the transaction that announces the account. So
    # whitenoise goes immediately *after* it — the earliest legal slot. Static
    # requests therefore pay for the RLS transaction, which is the price of the
    # invariant holding for every request without exception.
    MIDDLEWARE.insert(
        MIDDLEWARE.index("rls.middleware.RowLevelSecurityMiddleware") + 1,
        "whitenoise.middleware.WhiteNoiseMiddleware",
    )
    STORAGES["staticfiles"] = {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    }

# Uploaded CVs and job postings: 20 MB is generous for a DOCX or PDF.
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Date input formats accepted by the forms, most explicit first.
DATE_INPUT_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

# --- Logging ----------------------------------------------------------------
# Django's default configuration routes request errors to ``mail_admins`` and
# filters them out of the console whenever DEBUG is off. With no ADMINS set that
# means a 500 in production leaves no trace anywhere. The host captures stdout,
# so send them there instead — a deployed traceback is worth more than an email
# nobody configured.
if not DEBUG:
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "console": {"format": "[{levelname}] {name}: {message}", "style": "{"},
        },
        "handlers": {
            "console": {"class": "logging.StreamHandler", "formatter": "console"},
        },
        "root": {"handlers": ["console"], "level": "INFO"},
        "loggers": {
            # propagate=False: without it every request error is printed twice,
            # once here and once by the root logger.
            "django.request": {
                "handlers": ["console"],
                "level": "ERROR",
                "propagate": False,
            },
        },
    }

# --- Email ------------------------------------------------------------------
# Password resets in accounts mode, and the relance reminders, are the only
# things that send. Development prints to the console rather than needing a
# relay; a deployment sets the credentials and gets SMTP.
_email_host = os.environ.get("JOBHUNT_EMAIL_HOST", "")
EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend"
    if _email_host
    else "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = _email_host
EMAIL_PORT = int(os.environ.get("JOBHUNT_EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.environ.get("JOBHUNT_EMAIL_TLS", "1") == "1"
EMAIL_HOST_USER = os.environ.get("JOBHUNT_EMAIL_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("JOBHUNT_EMAIL_PASSWORD", "")
EMAIL_TIMEOUT = int(os.environ.get("JOBHUNT_EMAIL_TIMEOUT", "10"))
# The From: domain has to be the one DKIM-signed by the sender, or the message
# fails DMARC alignment and lands in spam.
DEFAULT_FROM_EMAIL = os.environ.get("JOBHUNT_FROM_EMAIL", "TonJobIdeal <bonjour@tonjobideal.com>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# --- JobHunt-specific knobs -------------------------------------------------
# ``runserver`` applies pending migrations itself before serving (and after
# each reload). On by default in development against a local database, never
# in production nor when a DEBUG checkout points at a remote server — its
# schema is not something a reload should touch.
AUTO_MIGRATE = (
    os.environ.get(
        "JOBHUNT_AUTO_MIGRATE",
        "1" if auto_migrate_default(DEBUG, DATABASES["default"].get("HOST", "")) else "0",
    )
    == "1"
)
# Where the legacy spreadsheet and offer folders live, used by `import_legacy`.
LEGACY_ROOT = BASE_DIR
LEGACY_WORKBOOK = BASE_DIR / "00_Suivi_candidatures.xlsx"

# Defaults seeded into a new profile's preferences; each profile then adjusts
# its own in the settings page.
# An application sitting in "sent" this long with no news is flagged as stale.
STALE_AFTER_DAYS = int(os.environ.get("JOBHUNT_STALE_AFTER_DAYS", "14"))
# Default gap between sending an application and the suggested follow-up.
DEFAULT_FOLLOW_UP_DAYS = int(os.environ.get("JOBHUNT_FOLLOW_UP_DAYS", "10"))
# How far from home an offer is worth a look.
DEFAULT_SEARCH_RADIUS_KM = int(os.environ.get("JOBHUNT_SEARCH_RADIUS_KM", "40"))

# --- Row-level security -----------------------------------------------------
# On PostgreSQL every table holding an account's data carries a policy, and
# the web process connects as a role subject to it (see the README, « Isolation
# des données »). The role the migrations create and grant:
RLS_APP_ROLE = os.environ.get("JOBHUNT_DB_APP_ROLE", "jobhunt_app")
# Refuse to serve when the policies are not in force (role that bypasses
# them, table without policy): on by default for a shared deployment. Off, a
# warning is logged once instead — development, and the test suite, whose
# connection has to be a superuser to create the test database.
RLS_ENFORCE = (
    os.environ.get("JOBHUNT_RLS_ENFORCE", "1" if AUTH_MODE == "accounts" and not DEBUG else "0")
    == "1"
)
# On PostgreSQL the test client makes every request as RLS_APP_ROLE, so the
# page tests exercise the policies; on SQLite the runner is the stock one.
TEST_RUNNER = "rls.testing.TestRunner"

# --- Persistence ------------------------------------------------------------
# The adapter behind the ports of ``tracker/ports.py`` (see
# ``tracker/adapters/``). A seam for tests and extensions — the in-memory
# adapter runs the rules without a database — NOT the engine switch: SQLite
# and PostgreSQL both go through the ORM adapter and are chosen by
# ``JOBHUNT_DATABASE_URL`` above.
PERSISTENCE_ADAPTER = "tracker.adapters.django_orm.DjangoPersistence"
