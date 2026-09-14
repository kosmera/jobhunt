"""Production authentication cannot fall back to passwords or an unverified session."""

from django.contrib.auth import authenticate
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.backends import AccountsBackend
from accounts.models import Profile
from accounts.services import LOCAL_BACKEND
from accounts.testing import make_user
from rls import as_user
from rls.testing import app_role


@override_settings(
    AUTH_MODE="accounts", DEBUG=False, IS_SAAS_PRODUCTION=False, PASSWORDLESS_AUTH=True,
    AUTHENTICATION_BACKENDS=[LOCAL_BACKEND],
)
class ProductionBackendTests(TestCase):
    def setUp(self):
        self.user = make_user("Camille", email="camille@example.org")
        self.backend = AccountsBackend()

    def test_password_authentication_refuses_an_existing_hash(self):
        User.objects.filter(pk=self.user.pk).update(password=make_password("existing-password"))
        self.assertIsNone(authenticate(username=self.user.username, password="existing-password"))

    def test_user_creation_and_password_changes_store_no_usable_password(self):
        created = User.objects.create_user(username="another", password="new-password")
        created.refresh_from_db()
        self.assertFalse(created.has_usable_password())
        self.user.set_password("replacement-password")
        self.user.save(update_fields=["password"])
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())

    def test_partial_user_save_also_clears_a_legacy_hash(self):
        User.objects.filter(pk=self.user.pk).update(password=make_password("legacy-password"))
        self.user.refresh_from_db()
        self.user.first_name = "Camille"
        self.user.save(update_fields=["first_name"])
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())
        self.assertEqual(self.user.first_name, "Camille")

    def test_unverified_email_cannot_restore_a_session(self):
        self.assertIsNone(self.backend.get_user(self.user.pk))
        Profile.objects.filter(user=self.user).update(verified_email=self.user.email)
        self.assertIsNone(self.backend.get_user(self.user.pk))

    def test_verified_email_restores_session_under_the_application_role(self):
        Profile.objects.filter(user=self.user).update(
            verified_email="Camille@Example.org", email_verified_at=timezone.now(),
        )
        with app_role(), as_user(None):
            self.assertEqual(self.backend.get_user(self.user.pk), self.user)

    def test_changed_email_and_inactive_account_cannot_restore_session(self):
        Profile.objects.filter(user=self.user).update(
            verified_email=self.user.email, email_verified_at=timezone.now(),
        )
        User.objects.filter(pk=self.user.pk).update(email="unverified@example.org")
        self.assertIsNone(self.backend.get_user(self.user.pk))
        User.objects.filter(pk=self.user.pk).update(email=self.user.email, is_active=False)
        self.assertIsNone(self.backend.get_user(self.user.pk))


@override_settings(
    AUTH_MODE="accounts", DEBUG=True, IS_SAAS_PRODUCTION=False, PASSWORDLESS_AUTH=False,
    AUTHENTICATION_BACKENDS=[LOCAL_BACKEND],
)
class DevelopmentBackendTests(TestCase):
    def test_development_passwords_remain_available(self):
        user = make_user("Camille", password="development-password")
        self.assertTrue(user.has_usable_password())
        self.assertEqual(authenticate(username=user.username, password="development-password"), user)

    @override_settings(AUTH_MODE="local", DEBUG=False)
    def test_local_sessions_do_not_require_an_email(self):
        user = make_user("Camille")
        self.assertEqual(AccountsBackend().get_user(user.pk), user)
