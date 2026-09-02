"""Helpers for data migrations, written against historical models.

Nothing here touches ``accounts.models`` directly: a migration gets its own
frozen versions through ``apps.get_model``.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.utils import timezone

from accounts.services import LOCAL_USERNAME


def owner_for_orphans(apps):
    """The account that inherits rows recorded before accounts existed.

    A database with exactly one account (a superuser made for the admin, say)
    hands the data to it. Otherwise a password-less ``local`` account is
    created — or reused — to hold it until someone completes its profile.
    """
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Profile = apps.get_model("accounts", "Profile")
    Preferences = apps.get_model("accounts", "Preferences")

    users = list(User.objects.order_by("pk")[:2])
    if len(users) == 1:
        user = users[0]
    else:
        user, _ = User.objects.get_or_create(
            username=LOCAL_USERNAME,
            defaults={
                "password": make_password(None),
                "is_active": True,
                "date_joined": timezone.now(),
            },
        )
    Profile.objects.get_or_create(user=user)
    Preferences.objects.get_or_create(user=user)
    return user
