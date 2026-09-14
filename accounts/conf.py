"""How people get into the application.

Two modes, chosen by ``settings.AUTH_MODE``:

- ``local`` — a trusted machine. No password anywhere: the first visit creates
  the profile, a single profile is signed in automatically, several profiles
  are picked from a list.
- ``accounts`` — a shared deployment. Sign-in and sign-up forms, passwords,
  the usual.

Everything mode-specific goes through these helpers so the rule lives in
one place.
"""

from __future__ import annotations

from django.conf import settings

LOCAL = "local"
ACCOUNTS = "accounts"


def is_local() -> bool:
    return settings.AUTH_MODE == LOCAL


def premium_by_default() -> bool:
    """Trusted local installations include Premium, outside commercial SaaS."""
    return is_local() and not settings.IS_SAAS_PRODUCTION


def signup_open() -> bool:
    return not is_local() and bool(settings.SIGNUP_OPEN)


def collect_launch_interest() -> bool:
    """Production collection is opt-in independently of commercial SaaS features."""
    return not is_local() and bool(settings.LAUNCH_INTEREST_ENABLED)
