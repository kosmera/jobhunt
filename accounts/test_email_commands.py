import io
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from django_q.models import OrmQ

from accounts.checks import check_email_delivery
from accounts.email_delivery import enqueue_launch_emails
from accounts.models import EmailJobKind, EmailJobStatus, LaunchEmailJob, Profile
from accounts.testing import make_user


class EmailRecoveryCommandTests(TestCase):
    def setUp(self):
        self.user = make_user(launch_email="contact@example.org", launch_plan="free", launch_consent_at=timezone.now())

    def command(self, name, *args):
        out = io.StringIO()
        call_command(name, *args, stdout=out)
        return out.getvalue()

    def test_backfill_is_scoped_and_repeatable(self):
        other = make_user(launch_email="other@example.org", launch_plan="premium", launch_consent_at=timezone.now())
        self.command("reconcile_launch_emails", "--backfill", "--owner-id", str(self.user.pk))
        self.command("reconcile_launch_emails", "--backfill", "--owner-id", str(self.user.pk))
        self.assertEqual(LaunchEmailJob.objects.filter(user=self.user).count(), 2)
        self.assertFalse(LaunchEmailJob.objects.filter(user=other).exists())
        self.assertEqual(OrmQ.objects.count(), 2)
        self.command("reconcile_launch_emails", "--backfill")
        self.assertEqual(LaunchEmailJob.objects.filter(user=other).count(), 2)

    def test_reaper_without_backfill_does_not_enrol_historical_contacts(self):
        self.command("reconcile_launch_emails")
        self.assertFalse(LaunchEmailJob.objects.exists())

    def test_watch_resets_failures_only_once(self):
        with patch("accounts.management.commands.reconcile_launch_emails.reconcile_user", return_value=0) as reconcile, patch(
            "accounts.management.commands.reconcile_launch_emails.close_old_connections",
        ), patch("accounts.management.commands.reconcile_launch_emails.time.sleep", side_effect=[None, KeyboardInterrupt]):
            self.command("reconcile_launch_emails", "--watch", "--retry-failed")
        self.assertEqual([call.kwargs["retry_failed"] for call in reconcile.call_args_list], [True, False])

    def uncertain_job(self):
        enqueue_launch_emails(Profile.objects.get(user=self.user))
        job = LaunchEmailJob.objects.get(user=self.user, kind=EmailJobKind.WELCOME)
        job.status = EmailJobStatus.UNCERTAIN
        job.attempts = 2
        job.queued_at = timezone.now() - timedelta(days=1)
        job.save()
        OrmQ.objects.all().delete()
        return job

    def resolve(self, job, outcome, user_id=None):
        return self.command("resolve_launch_email", "--owner-id", str(user_id or self.user.pk), "--job-id", str(job.pk), outcome)

    def test_confirmed_delivery_creates_no_new_message(self):
        job = self.uncertain_job()
        self.resolve(job, "--delivered")
        job.refresh_from_db()
        self.assertEqual(job.status, EmailJobStatus.SUCCEEDED)
        self.assertEqual(job.error_code, "manually_confirmed")
        self.assertFalse(OrmQ.objects.exists())

    def test_confirmed_absence_requeues_only_once(self):
        job = self.uncertain_job()
        self.resolve(job, "--not-delivered")
        job.refresh_from_db()
        self.assertEqual(job.status, EmailJobStatus.PENDING)
        self.assertEqual(job.attempts, 0)
        self.assertEqual(OrmQ.objects.count(), 1)
        with self.assertRaises(CommandError):
            self.resolve(job, "--not-delivered")

    def test_resolution_refuses_wrong_owner(self):
        job = self.uncertain_job()
        other = make_user()
        with self.assertRaises(CommandError):
            self.resolve(job, "--delivered", other.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, EmailJobStatus.UNCERTAIN)

    def test_confirmed_absence_does_not_send_after_withdrawal(self):
        job = self.uncertain_job()
        profile = Profile.objects.get(user=self.user)
        profile.launch_consent_at = None
        profile.save(update_fields=["launch_consent_at"])
        self.resolve(job, "--not-delivered")
        job.refresh_from_db()
        self.assertEqual(job.status, EmailJobStatus.CANCELLED)
        self.assertFalse(OrmQ.objects.exists())


class EmailConfigurationTests(SimpleTestCase):
    def test_default_queue_configuration_is_valid(self):
        self.assertEqual(check_email_delivery(None), [])

    def test_queue_must_share_the_consent_database_and_be_async(self):
        for config in ({"orm": "other"}, {"orm": "default", "sync": True}):
            with self.subTest(config=config), override_settings(Q_CLUSTER=config):
                self.assertIn("accounts.E002", [problem.id for problem in check_email_delivery(None)])

    def test_invalid_public_url_is_rejected(self):
        for url in ("javascript:alert(1)", "https://user:secret@example.org", "https://[bad", "https://example.org/?token=secret"):
            with self.subTest(url=url), override_settings(JOBHUNT_PUBLIC_URL=url):
                self.assertIn("accounts.E005", [problem.id for problem in check_email_delivery(None)])

    @override_settings(JOBHUNT_BREVO_LAUNCH_LIST_ID=0)
    def test_invalid_list_id_is_rejected(self):
        self.assertIn("accounts.E004", [problem.id for problem in check_email_delivery(None)])
