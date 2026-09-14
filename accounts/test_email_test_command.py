"""Local email testing never needs onboarding or touches unrelated accounts."""

import io
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from django_q.models import OrmQ

from accounts.brevo import BrevoError, ContactSyncResult
from accounts.email_delivery import enqueue_launch_emails
from accounts.management.commands import test_launch_emails as command
from accounts.models import EmailJobStatus, LaunchEmailJob, Profile
from accounts.testing import make_user


@override_settings(DEBUG=True, IS_SAAS_PRODUCTION=False, AUTH_MODE="local")
class LocalEmailTestCommandTests(TransactionTestCase):
    def run_command(self, *args):
        out = io.StringIO()
        call_command("test_launch_emails", "--to", "test@example.org", *args, stdout=out)
        return out.getvalue()

    def test_default_preview_renders_actual_template_without_data_or_network(self):
        with patch("accounts.email_delivery.send_welcome") as welcome, patch("accounts.email_delivery.sync_contact") as sync:
            output = self.run_command()
        self.assertIn("Premium", output)
        self.assertIn("24,90 €", output)
        self.assertIn("aucun compte créé", output)
        self.assertFalse(User.objects.exists())
        self.assertFalse(LaunchEmailJob.objects.exists())
        self.assertFalse(OrmQ.objects.exists())
        welcome.assert_not_called()
        sync.assert_not_called()

    def test_send_runs_real_jobs_outside_transaction_and_keeps_other_accounts_untouched(self):
        other = make_user(
            "Existing", email="real@example.org", launch_email="other@example.org",
            launch_plan="free", launch_consent_at=timezone.now(),
        )
        enqueue_launch_emails(Profile.objects.get(user=other))
        profile_before = Profile.objects.values().get(user=other)
        OrmQ.objects.create(key="unrelated", payload="unrelated-signed-message")
        unrelated_message_ids = set(OrmQ.objects.values_list("pk", flat=True))

        def outside_transaction(*args):
            self.assertFalse(connection.in_atomic_block)
            return ContactSyncResult(42, False)

        with patch("accounts.email_delivery.send_welcome", side_effect=outside_transaction) as welcome, patch(
            "accounts.email_delivery.sync_contact", side_effect=outside_transaction,
        ) as sync:
            output = self.run_command("--send", "--plan", "free")
        welcome.assert_called_once()
        sync.assert_called_once_with("test@example.org")
        self.assertIn("succeeded", output)
        self.assertEqual(LaunchEmailJob.objects.filter(status=EmailJobStatus.SUCCEEDED).count(), 2)
        self.assertEqual(Profile.objects.values().get(user=other), profile_before)
        self.assertEqual(set(OrmQ.objects.values_list("pk", flat=True)), unrelated_message_ids)
        self.assertEqual(LaunchEmailJob.objects.filter(user=other, status=EmailJobStatus.PENDING).count(), 2)
        test_user = User.objects.exclude(pk=other.pk).get()
        self.assertFalse(test_user.has_usable_password())
        self.assertFalse(test_user.is_staff)

    def test_repeated_send_reuses_account_and_does_not_repeat_successful_effects(self):
        with patch("accounts.email_delivery.send_welcome") as welcome, patch(
            "accounts.email_delivery.sync_contact", return_value=ContactSyncResult(42, False),
        ) as sync:
            self.run_command("--send")
            self.run_command("--send")
        welcome.assert_called_once()
        sync.assert_called_once()
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(LaunchEmailJob.objects.count(), 2)
        self.assertFalse(OrmQ.objects.exists())

    def test_explicit_failed_retry_does_not_resend_welcome(self):
        with patch("accounts.email_delivery.send_welcome") as welcome, patch(
            "accounts.email_delivery.sync_contact", side_effect=BrevoError("brevo_missing_api_key", retryable=False),
        ):
            with self.assertRaises(CommandError):
                self.run_command("--send")
            with patch("accounts.email_delivery.sync_contact", return_value=ContactSyncResult(42, False)) as sync:
                self.run_command("--send", "--retry-failed")
                sync.assert_called_once()
        welcome.assert_called_once()
        self.assertEqual(LaunchEmailJob.objects.filter(status=EmailJobStatus.SUCCEEDED).count(), 2)

    def test_uncertain_welcome_has_nonzero_exit_and_is_not_retried(self):
        from accounts.email_delivery import DeliveryError

        with patch("accounts.email_delivery.send_welcome", side_effect=DeliveryError(
            "smtp_delivery_unknown", EmailJobStatus.UNCERTAIN,
        )) as welcome, patch("accounts.email_delivery.sync_contact", return_value=ContactSyncResult(42, False)):
            with self.assertRaises(CommandError):
                self.run_command("--send")
            with self.assertRaises(CommandError):
                self.run_command("--send", "--retry-failed")
        welcome.assert_called_once()

    def test_existing_user_collision_is_refused_without_modifying_profile(self):
        user = make_user("Existing", username=command._username("test@example.org"), email="test@example.org")
        before = Profile.objects.values().get(user=user)
        with self.assertRaises(CommandError):
            self.run_command("--send")
        self.assertEqual(Profile.objects.values().get(user=user), before)
        self.assertFalse(LaunchEmailJob.objects.exists())

    def test_existing_test_account_is_not_mutated_for_a_new_plan(self):
        with patch("accounts.email_delivery.send_welcome"), patch(
            "accounts.email_delivery.sync_contact", return_value=ContactSyncResult(42, False),
        ):
            self.run_command("--send")
        before = Profile.objects.values().get()
        with self.assertRaises(CommandError):
            self.run_command("--send", "--plan", "free")
        self.assertEqual(Profile.objects.values().get(), before)

    def test_production_and_debug_disabled_are_refused_even_for_preview(self):
        for values in ({"DEBUG": False}, {"IS_SAAS_PRODUCTION": True}):
            with self.subTest(values=values), override_settings(**values), self.assertRaises(CommandError):
                self.run_command()

    def test_remote_database_and_hosted_environment_are_refused(self):
        remote = SimpleNamespace(vendor="postgresql", settings_dict={"HOST": "database.postgres.database.azure.com"})
        with patch.object(command, "connection", remote), self.assertRaises(CommandError):
            self.run_command("--send")
        with patch.dict(command.os.environ, {"WEBSITE_INSTANCE_ID": "hosted"}), self.assertRaises(CommandError):
            self.run_command("--send")
        self.assertFalse(User.objects.exists())

    def test_invalid_destination_and_retry_without_send_are_refused(self):
        for args in (("--to", "invalid"), ("--retry-failed",)):
            with self.subTest(args=args), self.assertRaises(CommandError):
                self.run_command(*args)
        self.assertFalse(User.objects.exists())

    def test_queue_failure_rolls_back_disposable_account_and_jobs(self):
        with patch("accounts.email_delivery.async_task", side_effect=RuntimeError("queue unavailable")):
            with self.assertRaises(RuntimeError):
                self.run_command("--send")
        self.assertFalse(User.objects.exists())
        self.assertFalse(LaunchEmailJob.objects.exists())
        self.assertFalse(OrmQ.objects.exists())
