"""Durable opt-in delivery, tenant boundaries and ambiguous SMTP outcomes."""

import smtplib
import uuid
from datetime import timedelta
from unittest.mock import Mock, patch

from django.core import mail
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import DatabaseError, connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.template import TemplateDoesNotExist
from django.utils import timezone
from django_q.models import OrmQ
from django_q.signing import SignedPackage

from accounts import email_delivery as delivery
from accounts.brevo import BrevoError, ContactSyncResult
from accounts.models import EmailJobKind, EmailJobStatus, LaunchEmailJob, Profile, SearchProfile
from accounts.onboarding.testing import all_answers
from accounts.services import finish_onboarding, has_premium
from accounts.testing import make_user


EMAIL_SETTINGS = {
    "AUTH_MODE": "accounts", "IS_SAAS_PRODUCTION": True,
    "LAUNCH_INTEREST_ENABLED": True,
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "EMAIL_HOST": "smtp.example.org", "JOBHUNT_PUBLIC_URL": "https://tonjobideal.com",
    "JOBHUNT_EMAIL_MAX_ATTEMPTS": 3, "JOBHUNT_EMAIL_JOB_TIMEOUT": 60,
}


def opted_in_user():
    return make_user(
        "Candidate", email="login@example.org", launch_email="contact@example.org",
        launch_plan="premium", launch_consent_at=timezone.now(),
    )


@override_settings(**EMAIL_SETTINGS)
class LaunchEmailQueueTests(TestCase):
    def setUp(self):
        self.user = make_user("Candidate", email="login@example.org", onboarded=False)
        self.answers = {
            **all_answers(), "launch_notify": True,
            "launch_email": "Contact@Example.org", "launch_plan": "premium",
        }

    def test_onboarding_atomically_creates_two_jobs_and_signed_id_only_messages(self):
        with patch.object(delivery, "send_welcome") as welcome, patch.object(delivery, "sync_contact") as sync:
            profile = finish_onboarding(self.user, self.answers)
        welcome.assert_not_called()
        sync.assert_not_called()
        self.assertEqual(LaunchEmailJob.objects.count(), 2)
        self.assertEqual(OrmQ.objects.count(), 2)
        for row in OrmQ.objects.all():
            package = SignedPackage.loads(row.payload)
            job = LaunchEmailJob.objects.get(task_id=package["id"])
            self.assertEqual(package["func"], "accounts.email_delivery.execute_queued")
            self.assertEqual(package["args"], (job.pk, self.user.pk))
            self.assertEqual(package["kwargs"], {})
            self.assertFalse(package["sync"])
            self.assertTrue(package["ack_failure"])
            self.assertEqual(package["timeout"], 60)
            self.assertNotIn("contact@example.org", repr(package))
            self.assertNotIn("login@example.org", repr(package))
            self.assertEqual(job.email, "contact@example.org")
            self.assertEqual(job.consent_at, profile.launch_consent_at)
            self.assertIsNotNone(job.queued_at)

    def test_repeated_completion_does_not_duplicate_jobs_or_messages(self):
        first = finish_onboarding(self.user, self.answers)
        second = finish_onboarding(self.user, self.answers)
        self.assertEqual(first.launch_consent_at, second.launch_consent_at)
        self.assertEqual(delivery.enqueue_launch_emails(second), 0)
        self.assertEqual(LaunchEmailJob.objects.count(), 2)
        self.assertEqual(OrmQ.objects.count(), 2)

    def test_later_onboarding_failure_rolls_back_consent_jobs_and_messages(self):
        with patch.object(SearchProfile, "full_clean", side_effect=ValidationError("invalid")):
            with self.assertRaises(ValidationError):
                finish_onboarding(self.user, self.answers)
        self.assertFalse(LaunchEmailJob.objects.exists())
        self.assertFalse(OrmQ.objects.exists())
        profile = Profile.objects.get(user=self.user)
        self.assertFalse(profile.is_onboarded)
        self.assertIsNone(profile.launch_consent_at)

    def test_second_broker_failure_rolls_back_first_job_message_and_onboarding(self):
        real_enqueue = delivery.async_task
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise DatabaseError("broker unavailable")
            return real_enqueue(*args, **kwargs)

        with patch.object(delivery, "async_task", side_effect=fail_second):
            with self.assertRaises(DatabaseError):
                finish_onboarding(self.user, self.answers)
        self.assertEqual(calls, 2)
        self.assertFalse(LaunchEmailJob.objects.exists())
        self.assertFalse(OrmQ.objects.exists())
        profile = Profile.objects.get(user=self.user)
        self.assertFalse(profile.is_onboarded)
        self.assertIsNone(profile.launch_consent_at)

    def test_skipped_or_non_boolean_consent_does_not_queue_email(self):
        for consent in (False, None, "true", 1):
            with self.subTest(consent=consent):
                finish_onboarding(self.user, {**self.answers, "launch_notify": consent})
        self.assertFalse(LaunchEmailJob.objects.exists())
        self.assertFalse(OrmQ.objects.exists())

    def test_backfill_queues_existing_opt_in_once_and_skips_inactive_user(self):
        user = opted_in_user()
        self.assertEqual(delivery.reconcile_user(user.pk, backfill=True), 2)
        self.assertEqual(delivery.reconcile_user(user.pk, backfill=True), 0)
        inactive = opted_in_user()
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])
        self.assertEqual(delivery.reconcile_user(inactive.pk, backfill=True), 0)
        self.assertFalse(LaunchEmailJob.objects.filter(user=inactive).exists())


@override_settings(**EMAIL_SETTINGS)
class LaunchEmailWorkerTests(TransactionTestCase):
    def setUp(self):
        self.user = opted_in_user()
        delivery.enqueue_launch_emails(Profile.objects.get(user=self.user))
        self.welcome = LaunchEmailJob.objects.get(user=self.user, kind=EmailJobKind.WELCOME)
        self.contact = LaunchEmailJob.objects.get(user=self.user, kind=EmailJobKind.CONTACT_SYNC)

    def make_due(self, job):
        LaunchEmailJob.objects.filter(pk=job.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))

    def test_duplicate_messages_deliver_each_effect_once(self):
        with patch.object(delivery, "send_welcome") as welcome, patch.object(
            delivery, "sync_contact", return_value=ContactSyncResult(42, False),
        ) as sync:
            for job in (self.welcome, self.contact, self.welcome, self.contact):
                delivery.execute_queued(job.pk, self.user.pk)
        welcome.assert_called_once()
        sync.assert_called_once_with("contact@example.org")
        for job in (self.welcome, self.contact):
            job.refresh_from_db()
            self.assertEqual(job.status, EmailJobStatus.SUCCEEDED)
            self.assertEqual(job.attempts, 1)
            self.assertIsNotNone(job.finished_at)

    def test_network_operations_run_outside_database_transaction(self):
        def assert_outside_transaction(*args):
            self.assertFalse(connection.in_atomic_block)
            return ContactSyncResult(42, False)

        with patch.object(delivery, "send_welcome", side_effect=assert_outside_transaction), patch.object(
            delivery, "sync_contact", side_effect=assert_outside_transaction,
        ):
            delivery.execute_queued(self.welcome.pk, self.user.pk)
            delivery.execute_queued(self.contact.pk, self.user.pk)
        self.assertEqual(LaunchEmailJob.objects.filter(status=EmailJobStatus.SUCCEEDED).count(), 2)

    def test_contact_retry_does_not_resend_successful_welcome(self):
        with patch.object(delivery, "send_welcome") as welcome, patch.object(
            delivery, "sync_contact", side_effect=BrevoError("brevo_http_503", retryable=True),
        ):
            delivery.execute_queued(self.welcome.pk, self.user.pk)
            delivery.execute_queued(self.contact.pk, self.user.pk)
            self.contact.refresh_from_db()
            self.assertEqual(self.contact.status, EmailJobStatus.RETRY)
            self.make_due(self.contact)
            self.assertEqual(delivery.reconcile_user(self.user.pk), 1)
            with patch.object(delivery, "sync_contact", return_value=ContactSyncResult(42, False)):
                delivery.execute_queued(self.contact.pk, self.user.pk)
                delivery.execute_queued(self.welcome.pk, self.user.pk)
        welcome.assert_called_once()
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.status, EmailJobStatus.SUCCEEDED)
        self.assertEqual(self.contact.attempts, 2)

    def test_transient_retries_wait_and_stop_at_configured_attempt_limit(self):
        with patch.object(delivery, "sync_contact", side_effect=BrevoError("brevo_http_429", retryable=True)) as sync:
            for attempt in range(1, 4):
                delivery.execute_queued(self.contact.pk, self.user.pk)
                self.contact.refresh_from_db()
                self.assertEqual(self.contact.attempts, attempt)
                if attempt < 3:
                    self.assertEqual(self.contact.status, EmailJobStatus.RETRY)
                    self.assertEqual(delivery.reconcile_user(self.user.pk), 0)
                    self.make_due(self.contact)
                    self.assertEqual(delivery.reconcile_user(self.user.pk), 1)
            self.assertEqual(self.contact.status, EmailJobStatus.FAILED)
            self.make_due(self.contact)
            self.assertEqual(delivery.reconcile_user(self.user.pk), 0)
            delivery.execute_queued(self.contact.pk, self.user.pk)
        self.assertEqual(sync.call_count, 3)

    def test_permanent_contact_failure_requires_explicit_recovery(self):
        with patch.object(delivery, "sync_contact", side_effect=BrevoError("brevo_http_401", retryable=False)):
            delivery.execute_queued(self.contact.pk, self.user.pk)
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.status, EmailJobStatus.FAILED)
        self.assertEqual(self.contact.error_code, "brevo_http_401")
        self.assertEqual(delivery.reconcile_user(self.user.pk), 0)
        self.assertEqual(delivery.reconcile_user(self.user.pk, retry_failed=True), 1)
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.status, EmailJobStatus.PENDING)
        self.assertEqual(self.contact.attempts, 0)

    def test_ambiguous_smtp_outcome_never_auto_retries_even_in_manual_failed_mode(self):
        with patch.object(delivery, "send_welcome", side_effect=delivery.DeliveryError(
            "smtp_delivery_unknown", EmailJobStatus.UNCERTAIN,
        )) as welcome:
            delivery.execute_queued(self.welcome.pk, self.user.pk)
            self.make_due(self.welcome)
            self.assertEqual(delivery.reconcile_user(self.user.pk, retry_failed=True), 0)
            delivery.execute_queued(self.welcome.pk, self.user.pk)
        welcome.assert_called_once()
        self.welcome.refresh_from_db()
        self.assertEqual(self.welcome.status, EmailJobStatus.UNCERTAIN)
        self.assertEqual(self.welcome.error_code, "smtp_delivery_unknown")

    def test_expired_worker_lease_marks_welcome_uncertain_and_contact_retryable(self):
        LaunchEmailJob.objects.filter(user=self.user).update(
            status=EmailJobStatus.RUNNING, attempts=1, claim_token=uuid.uuid4(),
            lease_until=timezone.now() - timedelta(seconds=1),
        )
        with patch.object(delivery, "send_welcome") as welcome, patch.object(delivery, "sync_contact") as sync:
            self.assertEqual(delivery.reconcile_user(self.user.pk), 0)
        welcome.assert_not_called()
        sync.assert_not_called()
        self.welcome.refresh_from_db()
        self.contact.refresh_from_db()
        self.assertEqual(self.welcome.status, EmailJobStatus.UNCERTAIN)
        self.assertEqual(self.contact.status, EmailJobStatus.RETRY)
        self.make_due(self.contact)
        self.assertEqual(delivery.reconcile_user(self.user.pk), 1)

    def test_inactive_or_changed_opt_in_cancels_before_network(self):
        for change in ({"launch_email": "changed@example.org"}, {"launch_consent_at": None},
                       {"launch_plan": "free"}, {"inactive": True}):
            with self.subTest(change=change):
                user = opted_in_user()
                delivery.enqueue_launch_emails(Profile.objects.get(user=user))
                job = LaunchEmailJob.objects.get(user=user, kind=EmailJobKind.WELCOME)
                if change.get("inactive"):
                    user.is_active = False
                    user.save(update_fields=["is_active"])
                else:
                    Profile.objects.filter(user=user).update(**change)
                with patch.object(delivery, "send_welcome") as welcome:
                    delivery.execute_queued(job.pk, user.pk)
                welcome.assert_not_called()
                job.refresh_from_db()
                self.assertEqual(job.status, EmailJobStatus.CANCELLED)

    def test_wrong_owner_cannot_claim_or_deliver_another_accounts_job(self):
        other = make_user("Other")
        with patch.object(delivery, "send_welcome") as welcome, patch.object(delivery, "sync_contact") as sync:
            delivery.execute_queued(self.welcome.pk, other.pk)
            delivery.execute_queued(self.contact.pk, other.pk)
        welcome.assert_not_called()
        sync.assert_not_called()
        self.welcome.refresh_from_db()
        self.assertEqual(self.welcome.status, EmailJobStatus.PENDING)
        self.assertEqual(self.welcome.attempts, 0)

    def test_blocked_brevo_contact_is_suppressed_without_failure_retry(self):
        with patch.object(delivery, "sync_contact", return_value=ContactSyncResult(42, True)):
            delivery.execute_queued(self.contact.pk, self.user.pk)
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.status, EmailJobStatus.SUPPRESSED)
        self.assertEqual(self.contact.error_code, "brevo_unsubscribed")
        self.assertEqual(delivery.reconcile_user(self.user.pk, retry_failed=True), 0)

    def test_lost_pending_message_is_requeued_without_creating_another_effect(self):
        OrmQ.objects.all().delete()
        LaunchEmailJob.objects.filter(user=self.user).update(queued_at=None)
        self.assertEqual(delivery.reconcile_user(self.user.pk), 2)
        self.assertEqual(LaunchEmailJob.objects.filter(user=self.user).count(), 2)
        self.assertEqual(OrmQ.objects.count(), 2)

    def test_real_welcome_template_confirms_interest_without_granting_paid_access(self):
        delivery.execute_queued(self.welcome.pk, self.user.pk)
        self.welcome.refresh_from_db()
        self.assertEqual(self.welcome.status, EmailJobStatus.SUCCEEDED)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        assert isinstance(message, EmailMultiAlternatives)
        self.assertEqual(message.to, ["contact@example.org"])
        self.assertIn("Premium", message.body)
        self.assertIn("https://tonjobideal.com", message.body)
        self.assertIn("24,90 € par mois", message.body)
        self.assertIn("Aucun abonnement ni paiement n'a été activé", message.body)
        self.assertEqual(message.alternatives[0][1], "text/html")
        self.user.refresh_from_db()
        self.assertFalse(has_premium(self.user))
        self.assertIsNone(Profile.objects.get(user=self.user).premium_until)


@override_settings(**EMAIL_SETTINGS)
class WelcomeSMTPTests(SimpleTestCase):
    def setUp(self):
        self.job = LaunchEmailJob(pk=12, email="contact@example.org", plan="premium")
        self.mailer = Mock()
        self.mailer.send_messages.return_value = 1
        self.connection_patch = patch.object(delivery, "get_connection", return_value=self.mailer)
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        self.template_patch = patch.object(delivery, "render_to_string", return_value="Welcome preview")
        self.template_patch.start()
        self.addCleanup(self.template_patch.stop)

    def assert_preflight_configuration_failure(self):
        with self.assertRaises(delivery.DeliveryError) as caught:
            delivery.send_welcome(self.job)
        self.assertEqual(str(caught.exception), "smtp_configuration_error")
        self.assertEqual(caught.exception.status, EmailJobStatus.FAILED)
        self.assertTrue(caught.exception.__suppress_context__)
        self.mailer.open.assert_not_called()
        self.mailer.send_messages.assert_not_called()

    def test_missing_template_is_failed_before_connecting_without_exposing_details(self):
        with patch.object(delivery, "render_to_string", side_effect=TemplateDoesNotExist("private/template/path")):
            self.assert_preflight_configuration_failure()

    @override_settings(DEFAULT_FROM_EMAIL="TonJobIdeal <bonjour@example.org>\r\nBcc: private@example.org")
    def test_invalid_from_header_is_failed_before_connecting_without_exposing_address(self):
        self.assert_preflight_configuration_failure()

    def test_bad_backend_is_failed_before_connecting_without_exposing_configuration(self):
        with patch.object(delivery, "get_connection", side_effect=ImproperlyConfigured("private backend secret")):
            self.assert_preflight_configuration_failure()

    def test_connect_failure_can_retry_because_data_was_never_sent(self):
        self.mailer.open.side_effect = OSError("secret connection details")
        with self.assertRaises(delivery.DeliveryError) as caught:
            delivery.send_welcome(self.job)
        self.assertEqual(caught.exception.code, "smtp_connection_failed")
        self.assertEqual(caught.exception.status, EmailJobStatus.RETRY)
        self.mailer.send_messages.assert_not_called()

    def test_server_accepted_send_stays_successful_if_quit_fails(self):
        self.mailer.close.side_effect = smtplib.SMTPServerDisconnected("quit failed")
        delivery.send_welcome(self.job)
        self.mailer.send_messages.assert_called_once()
        message = self.mailer.send_messages.call_args.args[0][0]
        self.assertEqual(message.extra_headers["Message-ID"], "<launch-welcome-12@tonjobideal.com>")
        self.assertEqual(message.to, ["contact@example.org"])

    def test_socket_or_disconnect_after_data_is_ambiguous(self):
        for error in (OSError("private transport"), smtplib.SMTPServerDisconnected("lost acknowledgement")):
            with self.subTest(error=type(error)):
                self.mailer.send_messages.side_effect = error
                with self.assertRaises(delivery.DeliveryError) as caught:
                    delivery.send_welcome(self.job)
                self.assertEqual(caught.exception.code, "smtp_delivery_unknown")
                self.assertEqual(caught.exception.status, EmailJobStatus.UNCERTAIN)

    def test_explicit_smtp_data_rejection_classifies_temporary_and_permanent_errors(self):
        for code, expected in ((451, EmailJobStatus.RETRY), (550, EmailJobStatus.FAILED)):
            with self.subTest(code=code):
                self.mailer.send_messages.side_effect = smtplib.SMTPDataError(code, b"private response")
                with self.assertRaises(delivery.DeliveryError) as caught:
                    delivery.send_welcome(self.job)
                self.assertEqual(caught.exception.code, "smtp_rejected")
                self.assertEqual(caught.exception.status, expected)

    def test_recipient_rejection_classifies_temporary_and_permanent_errors(self):
        for code, expected in ((450, EmailJobStatus.RETRY), (550, EmailJobStatus.FAILED)):
            with self.subTest(code=code):
                self.mailer.send_messages.side_effect = smtplib.SMTPRecipientsRefused({
                    "contact@example.org": (code, b"private response"),
                })
                with self.assertRaises(delivery.DeliveryError) as caught:
                    delivery.send_welcome(self.job)
                self.assertEqual(caught.exception.code, "smtp_recipient_rejected")
                self.assertEqual(caught.exception.status, expected)

    def test_authentication_rejection_is_permanent_without_sending(self):
        self.mailer.open.side_effect = smtplib.SMTPAuthenticationError(535, b"bad credentials")
        with self.assertRaises(delivery.DeliveryError) as caught:
            delivery.send_welcome(self.job)
        self.assertEqual(caught.exception.status, EmailJobStatus.FAILED)
        self.mailer.send_messages.assert_not_called()

    def test_unsupported_tls_is_configuration_failure_not_a_network_retry(self):
        self.mailer.open.side_effect = smtplib.SMTPNotSupportedError("STARTTLS unavailable")
        with self.assertRaises(delivery.DeliveryError) as caught:
            delivery.send_welcome(self.job)
        self.assertEqual(caught.exception.code, "smtp_configuration_error")
        self.assertEqual(caught.exception.status, EmailJobStatus.FAILED)
        self.mailer.send_messages.assert_not_called()

    @override_settings(EMAIL_HOST="")
    def test_missing_smtp_configuration_fails_without_opening_connection(self):
        with self.assertRaises(delivery.DeliveryError) as caught:
            delivery.send_welcome(self.job)
        self.assertEqual(caught.exception.code, "smtp_not_configured")
        self.mailer.open.assert_not_called()

    def test_backend_reporting_no_delivery_is_failed(self):
        self.mailer.send_messages.return_value = 0
        with self.assertRaises(delivery.DeliveryError) as caught:
            delivery.send_welcome(self.job)
        self.assertEqual(caught.exception.code, "smtp_no_message_sent")
        self.assertEqual(caught.exception.status, EmailJobStatus.FAILED)
