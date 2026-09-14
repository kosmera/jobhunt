"""Launch interest is an opt-in record, separate from login and paid access."""

import csv
import io
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Profile, SearchProfile
from accounts.onboarding.testing import all_answers
from accounts.services import finish_onboarding, has_premium
from accounts.testing import make_user


@override_settings(AUTH_MODE="accounts", IS_SAAS_PRODUCTION=True, LAUNCH_INTEREST_ENABLED=True)
class LaunchInterestDataTests(TestCase):
    def setUp(self):
        self.user = make_user("Candidate", email="login@example.org", onboarded=False)
        self.answers = {
            **all_answers(), "launch_notify": True,
            "launch_email": "Updates@Example.org", "launch_plan": "premium",
        }

    def test_capture_preserves_login_email_and_does_not_grant_premium(self):
        other = make_user("Other", onboarded=False)
        profile = finish_onboarding(self.user, self.answers)
        self.user.refresh_from_db()
        self.assertEqual(profile.launch_email, "updates@example.org")
        self.assertEqual(profile.launch_plan, "premium")
        self.assertIsNotNone(profile.launch_consent_at)
        self.assertTrue(profile.is_onboarded)
        self.assertEqual(self.user.email, "login@example.org")
        self.assertEqual(profile.subscription_level, "automatic")
        self.assertIsNone(profile.premium_until)
        self.assertFalse(has_premium(self.user))
        self.assertIsNone(Profile.objects.get(user=other).launch_consent_at)

    def test_free_plan_is_also_an_explicit_interest(self):
        profile = finish_onboarding(self.user, {**self.answers, "launch_plan": "free"})
        self.assertEqual(profile.launch_plan, "free")
        self.assertIsNotNone(profile.launch_consent_at)
        self.assertFalse(has_premium(self.user))

    def test_missing_false_and_non_boolean_consent_do_not_capture_contact_data(self):
        for consent in (None, False, "true", "on", 1):
            with self.subTest(consent=consent):
                profile = finish_onboarding(self.user, {**self.answers, "launch_notify": consent})
                self.assertEqual(profile.launch_email, "")
                self.assertEqual(profile.launch_plan, "")
                self.assertIsNone(profile.launch_consent_at)

    def test_completion_retries_preserve_first_consent_timestamp(self):
        first = finish_onboarding(self.user, self.answers)
        consent_at = first.launch_consent_at
        onboarded_at = first.onboarded_at
        with patch("accounts.services.timezone.now", return_value=timezone.now() + timedelta(days=1)):
            second = finish_onboarding(self.user, self.answers)
        self.assertEqual(second.launch_consent_at, consent_at)
        self.assertEqual(second.onboarded_at, onboarded_at)
        self.assertEqual(Profile.objects.filter(user=self.user).count(), 1)

    def test_invalid_contact_answers_roll_back_completion(self):
        for change in (
            {"launch_email": ""}, {"launch_email": "bad-address"}, {"launch_email": None},
            {"launch_email": "a" * 255 + "@example.org"}, {"launch_plan": "paid"},
        ):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                finish_onboarding(self.user, {**self.answers, **change})
            profile = Profile.objects.get(user=self.user)
            self.assertFalse(profile.is_onboarded)
            self.assertIsNone(profile.launch_consent_at)
            self.assertEqual(profile.launch_email, "")

    def test_later_onboarding_failure_rolls_back_contact_details(self):
        with patch.object(SearchProfile, "full_clean", side_effect=ValidationError("invalid")):
            with self.assertRaises(ValidationError):
                finish_onboarding(self.user, self.answers)
        profile = Profile.objects.get(user=self.user)
        self.assertFalse(profile.is_onboarded)
        self.assertEqual(profile.launch_email, "")
        self.assertIsNone(profile.launch_consent_at)

    @override_settings(AUTH_MODE="local", IS_SAAS_PRODUCTION=False)
    def test_local_completion_does_not_collect_launch_interest(self):
        profile = finish_onboarding(self.user, self.answers)
        self.assertIsNone(profile.launch_consent_at)
        self.assertEqual(profile.launch_email, "")
        self.assertTrue(has_premium(self.user))

    @override_settings(AUTH_MODE="accounts", IS_SAAS_PRODUCTION=False, LAUNCH_INTEREST_ENABLED=False)
    def test_development_accounts_mode_does_not_record_opt_in_or_queue_email(self):
        from accounts.models import LaunchEmailJob

        profile = finish_onboarding(self.user, self.answers)
        self.assertIsNone(profile.launch_consent_at)
        self.assertEqual(profile.launch_email, "")
        self.assertFalse(LaunchEmailJob.objects.exists())


class LaunchInterestExportTests(TestCase):
    def export(self):
        out = io.StringIO()
        call_command("export_launch_interest", stdout=out)
        return list(csv.DictReader(io.StringIO(out.getvalue())))

    def test_export_includes_only_consented_active_accounts_and_no_cv_or_login_details(self):
        now = timezone.now()
        opted_in = make_user(
            "Private name", email="login@example.org", launch_email="contact@example.org",
            launch_plan="premium", launch_consent_at=now,
        )
        make_user("No consent", launch_email="no@example.org", launch_plan="free")
        make_user("Missing email", launch_plan="premium", launch_consent_at=now)
        inactive = make_user(
            "Inactive", launch_email="inactive@example.org", launch_plan="free", launch_consent_at=now,
        )
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])
        self.assertEqual(self.export(), [{
            "user_id": str(opted_in.pk), "email": "contact@example.org",
            "plan": "premium", "consent_at": now.isoformat(),
        }])

    def test_formula_like_email_is_safe_to_open_in_a_spreadsheet(self):
        make_user(
            "Candidate", launch_email="=formula@example.org",
            launch_plan="free", launch_consent_at=timezone.now(),
        )
        self.assertEqual(self.export()[0]["email"], "'=formula@example.org")

    def test_export_is_read_only(self):
        user = make_user(
            launch_email="contact@example.org", launch_plan="premium", launch_consent_at=timezone.now(),
        )
        before = Profile.objects.values().get(user=user)
        self.export()
        self.assertEqual(Profile.objects.values().get(user=user), before)

    def test_postgres_runtime_role_is_refused_before_reading_profiles(self):
        report = SimpleNamespace(role=SimpleNamespace(superuser=False, bypass_rls=False), owned=[])
        out = io.StringIO()
        with patch.object(connection, "vendor", "postgresql"), patch(
            "accounts.management.commands.export_launch_interest.verify.inspect", return_value=report,
        ), self.assertNumQueries(0), self.assertRaisesMessage(CommandError, "identifiants de maintenance"):
            call_command("export_launch_interest", stdout=out)
        self.assertEqual(out.getvalue(), "")
