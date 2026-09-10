"""Premium entitlement is server-owned, account-scoped, and time-limited."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.forms import ProfileForm
from accounts.models import Profile
from accounts.services import has_premium
from accounts.testing import make_user


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

    def test_profile_form_cannot_grant_premium(self):
        user = make_user()
        form = ProfileForm(
            {"display_name": "Lionel", "premium_until": "2099-01-01T00:00:00Z"},
            instance=Profile.objects.get(user=user),
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertFalse(has_premium(user))
