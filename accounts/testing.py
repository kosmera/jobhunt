"""Fixtures shared by the test suites of the core and of the extensions."""

from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.services import preferences_for, profile_for, unique_username


def make_user(
    display_name: str = "Lionel",
    *,
    username: str | None = None,
    email: str = "",
    password: str | None = None,
    onboarded: bool = True,
    **profile_fields,
):
    user = User.objects.create_user(
        username=username or unique_username(display_name), email=email, password=password
    )
    profile = profile_for(user)
    profile.display_name = display_name
    for field, value in profile_fields.items():
        setattr(profile, field, value)
    if onboarded:
        profile.onboarded_at = timezone.now()
    profile.save()
    preferences_for(user)
    return user


@override_settings(AUTH_MODE="local")
class OwnedTestCase(TestCase):
    """A signed-in, onboarded account for tests that drive the pages."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = make_user("Lionel", username="lionel", location="Nivelles")

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
