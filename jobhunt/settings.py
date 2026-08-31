"""Django settings for the JobHunt application tracker.

Single-user, locally-hosted tool. SQLite by default; the ORM keeps the door
open for a PostgreSQL migration without touching application code.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# A local, single-user tool. Override via the environment for anything else.
SECRET_KEY = os.environ.get(
    "JOBHUNT_SECRET_KEY", "django-insecure-local-only-jobhunt-tracker-key"
)
DEBUG = os.environ.get("JOBHUNT_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]", "0.0.0.0", "testserver"]
CSRF_TRUSTED_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "tracker",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
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
                "tracker.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "jobhunt.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {"transaction_mode": "IMMEDIATE", "init_command": "PRAGMA journal_mode=WAL;"},
    }
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

# Uploaded CVs and job postings: 20 MB is generous for a DOCX or PDF.
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Date input formats accepted by the forms, most explicit first.
DATE_INPUT_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

# --- JobHunt-specific knobs -------------------------------------------------
# Where the legacy spreadsheet and offer folders live, used by `import_legacy`.
LEGACY_ROOT = BASE_DIR
LEGACY_WORKBOOK = BASE_DIR / "00_Suivi_candidatures.xlsx"

# An application sitting in "sent" this long with no news is flagged as stale.
STALE_AFTER_DAYS = int(os.environ.get("JOBHUNT_STALE_AFTER_DAYS", "14"))
# Default gap between sending an application and the suggested follow-up.
DEFAULT_FOLLOW_UP_DAYS = int(os.environ.get("JOBHUNT_FOLLOW_UP_DAYS", "10"))
