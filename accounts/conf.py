"""How people get into the application.

Two modes, chosen by ``settings.AUTH_MODE``:

- ``local`` — a trusted machine. No password anywhere: the first visit creates
  the profile, a single profile is signed in automatically, several profiles
  are picked from a list.
- ``accounts`` — a shared deployment. Sign-in and sign-up forms, passwords,
  the usual.

Everything mode-specific goes through these two helpers so the rule lives in
one place.
"""

from __future__ import annotations

from django.conf import settings

LOCAL = "local"
ACCOUNTS = "accounts"


def is_local() -> bool:
    return settings.AUTH_MODE == LOCAL


def signup_open() -> bool:
    return not is_local() and bool(settings.SIGNUP_OPEN)
