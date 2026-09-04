"""Django settings for the JobHunt application tracker.

Locally hosted by default (one profile, no password), deployable as a shared
instance with accounts — see ``AUTH_MODE`` below. SQLite by default,
PostgreSQL when ``JOBHUNT_DATABASE_URL`` says so — the engine is configuration
(``jobhunt.database``), the application code never chooses it.
"""

import os
from pathlib import Path

from jobhunt.database import auto_migrate_default, conn_max_age_from_env, database_config
from jobhunt.plugins import plugin_apps
from jobhunt.storage import PROVIDER_ENV, storage_config

BASE_DIR = Path(__file__).resolve().parent.parent

# A local tool first. Override via the environment for anything else.
SECRET_KEY = os.environ.get(
    "JOBHUNT_SECRET_KEY", "django-insecure-local-only-jobhunt-tracker-key"
)
DEBUG = os.environ.get("JOBHUNT_DEBUG", "1") == "1"


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

# Extensions hors dépôt (ex. le copilote IA) : installées comme paquets et
# découvertes par point d'entrée, jamais listées en dur ici.
INSTALLED_APPS += plugin_apps()

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

# Uploaded CVs and job postings: 20 MB is generous for a DOCX or PDF.
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Date input formats accepted by the forms, most explicit first.
DATE_INPUT_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

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
