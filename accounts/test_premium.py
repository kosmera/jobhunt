"""Premium defaults, administrator overrides, and expiration are account-scoped."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.forms import ProfileForm
from accounts.models import Profile
from accounts.services import has_premium
from accounts.testing import make_user


@override_settings(AUTH_MODE="accounts", IS_SAAS_PRODUCTION=False)
class PremiumEntitlementTests(TestCase):
    def test_only_a_current_paid_period_grants_access(self):
        user = make_user()
        other = make_user()
        now = timezone.now()
        with patch("accounts.services.timezone.now", return_value=now):
            for until, expected in (
                (None, False),
                (now - timedelta(seconds=1), False),
                (now, False),
                (now + timedelta(days=30), True),
                (None, False),
            ):
                with self.subTest(until=until):
                    Profile.objects.filter(user=user).update(premium_until=until)
                    self.assertEqual(has_premium(user), expected)
                    self.assertFalse(has_premium(other))

    @override_settings(DEBUG=True)
    def test_debug_staff_and_superuser_are_not_paid_entitlements(self):
        user = make_user()
        user.is_staff = user.is_superuser = True
        user.save()
        self.assertFalse(has_premium(user))

    def test_missing_profile_anonymous_and_disabled_accounts_have_no_access(self):
        self.assertFalse(has_premium(None))
        self.assertFalse(has_premium(AnonymousUser()))
        user = make_user(premium_until=timezone.now() + timedelta(days=30))
        # The same previously loaded account must see revocation immediately.
        type(user).objects.filter(pk=user.pk).update(is_active=False)
        self.assertFalse(has_premium(user))
        Profile.objects.filter(user=user).delete()
        self.assertFalse(has_premium(user))
        self.assertFalse(Profile.objects.filter(user=user).exists())

    def test_is_premium_property_follows_premium_until(self):
        user = make_user()
        profile = Profile.objects.get(user=user)
        now = timezone.now()
        with patch("accounts.models.timezone.now", return_value=now):
            for until, expected in (
                (None, False),
                (now - timedelta(seconds=1), False),
                (now, False),
                (now + timedelta(days=30), True),
            ):
                with self.subTest(until=until):
                    profile.premium_until = until
                    self.assertEqual(profile.is_premium, expected)
        # A property, not a column: nothing to migrate, nothing the form can set.
        self.assertNotIn("is_premium", [field.name for field in Profile._meta.get_fields()])

    def test_profile_form_cannot_grant_premium(self):
        user = make_user()
        form = ProfileForm(
            {"display_name": "Lionel", "subscription_level": "premium", "premium_until": "2099-01-01T00:00:00Z"},
            instance=Profile.objects.get(user=user),
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(Profile.objects.get(user=user).subscription_level, "automatic")
        self.assertFalse(has_premium(user))


@override_settings(AUTH_MODE="local", IS_SAAS_PRODUCTION=False)
class LocalPremiumTests(TestCase):
    def test_new_and_existing_local_profiles_are_premium_without_an_expiration(self):
        user = make_user()
        profile = Profile.objects.get(user=user)
        self.assertIsNone(profile.premium_until)
        self.assertTrue(profile.is_premium)
        self.assertTrue(has_premium(user))
        # Reading access does not invent or persist a paid period.
        profile.refresh_from_db()
        self.assertIsNone(profile.premium_until)

    @override_settings(DEBUG=False)
    def test_local_premium_does_not_depend_on_debug_or_administrator_status(self):
        user = make_user()
        self.assertFalse(user.is_staff)
        self.assertTrue(has_premium(user))

    def test_default_access_tracks_deployment_without_persisting_a_grant(self):
        user = make_user()
        profile = Profile.objects.get(user=user)
        for auth_mode, saas, expected in (
            ("local", False, True), ("accounts", False, False),
            ("accounts", True, False), ("local", True, False),
        ):
            with self.subTest(auth_mode=auth_mode, saas=saas), override_settings(
                AUTH_MODE=auth_mode, IS_SAAS_PRODUCTION=saas,
            ):
                self.assertEqual(has_premium(user), expected)
                self.assertEqual(profile.is_premium, expected)

    def test_disabled_deleted_and_missing_accounts_are_denied_locally(self):
        self.assertFalse(has_premium(None))
        self.assertFalse(has_premium(AnonymousUser()))
        user = make_user()
        type(user).objects.filter(pk=user.pk).update(is_active=False)
        self.assertFalse(has_premium(user))
        type(user).objects.filter(pk=user.pk).update(is_active=True)
        Profile.objects.filter(user=user).delete()
        self.assertFalse(has_premium(user))
        self.assertFalse(Profile.objects.filter(user=user).exists())

    def test_explicit_level_and_expiration_override_the_default_in_every_mode(self):
        user = make_user()
        now = timezone.now()
        for mode in ("local", "accounts"):
            with override_settings(AUTH_MODE=mode), patch("accounts.models.timezone.now", return_value=now):
                for level, until, expected in (
                    ("free", None, False),
                    ("free", now + timedelta(days=30), False),
                    ("premium", None, True),
                    ("premium", now + timedelta(days=30), True),
                    ("premium", now, False),
                    ("premium", now - timedelta(seconds=1), False),
                    ("automatic", now - timedelta(seconds=1), False),
                    ("automatic", now + timedelta(days=30), True),
                ):
                    with self.subTest(mode=mode, level=level, until=until):
                        Profile.objects.filter(user=user).update(
                            subscription_level=level, premium_until=until,
                        )
                        self.assertEqual(has_premium(user), expected)
                        self.assertEqual(Profile.objects.get(user=user).is_premium, expected)
