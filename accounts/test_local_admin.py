"""Local installation ownership is separate from sign-up and paid access."""

from datetime import datetime

from django.contrib.auth.models import AnonymousUser, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile
from accounts.onboarding.testing import walk
from accounts.services import ensure_local_admin, has_premium
from accounts.testing import make_user


@override_settings(AUTH_MODE="local", IS_SAAS_PRODUCTION=False)
class LocalAdminTests(TestCase):
    def test_onboarding_creates_a_premium_local_administrator(self):
        response = walk(self.client, name="Owner")
        self.assertEqual(response.status_code, 302)
        user = User.objects.get()
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertFalse(user.has_usable_password())
        self.assertTrue(has_premium(user))
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_existing_local_session_is_upgraded_even_if_another_admin_exists(self):
        owner = make_user(username="local")
        User.objects.create_superuser(username="manual-admin", password="test-password")
        self.client.force_login(owner)
        # Simulate a session established before automatic local administration.
        User.objects.filter(pk=owner.pk).update(is_staff=False, is_superuser=False)
        response = self.client.get(reverse("admin:login"), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.path, reverse("admin:index"))
        owner.refresh_from_db()
        self.assertTrue(owner.is_staff)
        self.assertTrue(owner.is_superuser)
        self.assertTrue(has_premium(owner))
        self.assertContains(self.client.get(reverse("tracker:dashboard")), "Données brutes")

    def test_direct_admin_login_uses_passwordless_local_entry(self):
        owner = make_user()
        response = self.client.get(reverse("admin:login"), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.path, reverse("admin:index"))
        self.assertEqual(response.wsgi_request.user.pk, owner.pk)

    def test_admin_login_with_multiple_profiles_uses_the_chooser(self):
        owner = make_user("Owner")
        make_user("Other")
        response = self.client.get(reverse("admin:login"), follow=True)
        self.assertTemplateUsed(response, "accounts/chooser.html")
        response = self.client.post(
            reverse("accounts:login"), {"user": owner.pk, "next": reverse("admin:index")},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.path, reverse("admin:index"))

    def test_additional_local_profile_is_not_an_admin(self):
        make_user("Owner")
        other = make_user("Other")
        self.client.force_login(other)
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.headers["Location"])
        other.refresh_from_db()
        self.assertFalse(other.is_staff)
        self.assertFalse(other.is_superuser)

    def test_inactive_owner_does_not_promote_another_profile(self):
        owner = make_user("Owner")
        owner.is_active = False
        owner.save(update_fields=["is_active"])
        other = make_user("Other")
        for user in (owner, other, AnonymousUser(), None):
            ensure_local_admin(user)
        self.assertFalse(User.objects.filter(is_staff=True).exists())

    def test_promotion_is_idempotent(self):
        owner = make_user()
        ensure_local_admin(owner)
        with self.assertNumQueries(0):
            ensure_local_admin(owner)

    @override_settings(DEBUG=False)
    def test_explicit_local_mode_works_without_debug(self):
        owner = make_user()
        ensure_local_admin(owner)
        owner.refresh_from_db()
        self.assertTrue(owner.is_superuser)

    @override_settings(AUTH_MODE="accounts", DEBUG=True)
    def test_accounts_mode_does_not_promote_even_the_first_user_named_local(self):
        user = make_user(username="local")
        ensure_local_admin(user)
        self.client.force_login(user)
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_admin_can_edit_subscription_level_and_expiration_in_both_modes(self):
        owner = make_user()
        ensure_local_admin(owner)
        self.client.force_login(owner)
        profile = Profile.objects.get(user=owner)
        url = reverse("admin:accounts_profile_change", args=[profile.pk])
        for mode in ("local", "accounts"):
            with override_settings(AUTH_MODE=mode):
                page = self.client.get(url)
                self.assertEqual(page.status_code, 200)
                self.assertIn("subscription_level", page.context["adminform"].form.fields)
                self.assertIn("premium_until", page.context["adminform"].form.fields)
                for level, date, expected in (
                    ("premium", "2099-01-01", True),
                    ("free", "2099-01-01", False),
                    ("premium", "2020-01-01", False),
                    ("premium", "", True),
                    ("automatic", "", mode == "local"),
                ):
                    with self.subTest(mode=mode, level=level, date=date):
                        response = self.client.post(url, {
                            "user": owner.pk, "display_name": "New name", "headline": "", "location": "",
                            "phone": "", "onboarded_at_0": "2026-09-07", "onboarded_at_1": "10:00:00",
                            "subscription_level": level, "premium_until_0": date,
                            "premium_until_1": "00:00:00" if date else "", "_save": "Save",
                        })
                        self.assertEqual(response.status_code, 302)
                        profile.refresh_from_db()
                        self.assertEqual(profile.display_name, "New name")
                        self.assertEqual(profile.subscription_level, level)
                        self.assertEqual(
                            profile.premium_until,
                            timezone.make_aware(datetime.fromisoformat(date)) if date else None,
                        )
                        self.assertEqual(has_premium(owner), expected)
