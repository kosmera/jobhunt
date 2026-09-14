"""Deployment defaults and the irreversible removal of historical password hashes."""

from importlib import import_module
import os
from pathlib import Path
import runpy
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.db import connection
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from accounts.checks import check_passwordless
from accounts.models import Profile
from accounts.views import email_client_ip
from accounts import magic_links


class PasswordlessDefaultsTests(SimpleTestCase):
    @override_settings(TRUST_AZURE_CLIENT_IP=False)
    def test_direct_server_ignores_spoofed_forwarded_addresses(self):
        request = RequestFactory().get("/", REMOTE_ADDR="192.0.2.1", HTTP_CLIENT_IP="198.51.100.1", HTTP_X_FORWARDED_FOR="203.0.113.1")
        self.assertEqual(email_client_ip(request), "192.0.2.1")

    @override_settings(TRUST_AZURE_CLIENT_IP=True)
    def test_azure_client_addresses_are_validated_and_ports_removed(self):
        for value, expected in (
            ("198.51.100.1:12345", "198.51.100.1"),
            ("[2001:db8::1]:443", "2001:db8::1"),
            ("2001:db8::1", "2001:db8::1"),
            ("forged, 198.51.100.1", "192.0.2.1"),
        ):
            request = RequestFactory().get("/", REMOTE_ADDR="192.0.2.1", HTTP_CLIENT_IP=value)
            self.assertEqual(email_client_ip(request), expected)

    def test_production_cannot_disable_passwordless_through_environment(self):
        path = Path(__file__).resolve().parents[1] / "jobhunt" / "settings.py"
        for debug, saas in (("0", "false"), ("1", "true")):
            with self.subTest(debug=debug, saas=saas), patch.dict(os.environ, {
                "JOBHUNT_DEBUG": debug, "IS_SAAS_PRODUCTION": saas, "JOBHUNT_PASSWORDLESS_AUTH": "0",
            }):
                self.assertTrue(runpy.run_path(str(path))["PASSWORDLESS_AUTH"])

    @override_settings(
        AUTH_MODE="accounts", PASSWORDLESS_AUTH=True, DEBUG=False,
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        JOBHUNT_PUBLIC_URL="http://example.org", SECRET_KEY="django-insecure-placeholder",
    )
    def test_production_refuses_unconfigured_mail_insecure_links_and_default_key(self):
        self.assertEqual({problem.id for problem in check_passwordless(None)}, {
            "accounts.E006", "accounts.E007", "accounts.E008",
        })


class PasswordPurgeTests(TestCase):
    def purge(self):
        migration = import_module("accounts.migrations.0009_profile_email_verified_at_profile_verified_email_and_more")
        migration.remove_production_passwords(apps, SimpleNamespace(connection=connection))

    @override_settings(AUTH_MODE="accounts", PASSWORDLESS_AUTH=True)
    def test_production_migration_removes_all_hashes_without_verifying_addresses(self):
        users = [User.objects.create_user(username=name, email=f"{name}@example.org") for name in ("member", "admin")]
        User.objects.all().update(password=make_password("historical-password"))
        User.objects.filter(pk=users[1].pk).update(is_superuser=True, is_staff=True)
        self.purge()
        self.assertTrue(all(not user.has_usable_password() for user in User.objects.all()))
        self.assertFalse(Profile.objects.exclude(verified_email="").exists())
        self.assertFalse(Profile.objects.filter(email_verified_at__isnull=False).exists())

    @override_settings(AUTH_MODE="local", PASSWORDLESS_AUTH=False)
    def test_migration_preserves_trusted_local_installations(self):
        user = User.objects.create_user(username="local-admin", password="local-password")
        self.purge()
        user.refresh_from_db()
        self.assertTrue(user.check_password("local-password"))

    @override_settings(AUTH_MODE="accounts", PASSWORDLESS_AUTH=True)
    def test_proof_normalizes_a_legacy_mixed_case_email(self):
        user = User.objects.create_user(username="legacy-user", email="Legacy@Example.org")
        magic_links.request_link("legacy@example.org")
        link = magic_links.EmailSignInLink.objects.get()
        self.assertIsNotNone(magic_links.consume_link(magic_links.sign_link(link)))
        user.refresh_from_db()
        self.assertEqual(user.email, "legacy@example.org")
