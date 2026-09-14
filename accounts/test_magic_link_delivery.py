"""One-use email links survive delivery failure without repeated concurrent sends."""

import io
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.mail.backends.console import EmailBackend as ConsoleBackend
from django.core.mail.backends.filebased import EmailBackend as FileBackend
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from django_q.models import OrmQ
from django_q.signing import SignedPackage

from accounts import magic_links
from accounts.models import EmailSignInLink
from accounts.testing import make_user


@override_settings(
    AUTH_MODE="accounts", PASSWORDLESS_AUTH=True, SIGNUP_OPEN=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    JOBHUNT_PUBLIC_URL="https://example.org",
)
class MagicLinkDeliveryTests(TestCase):
    def link(self, **fields):
        return EmailSignInLink.objects.create(**{
            "purpose": "signup", "email": "camille@example.org", "display_name": "Camille",
            "expires_at": timezone.now() + timedelta(minutes=15), **fields,
        })

    def test_duplicate_jobs_send_once(self):
        link = self.link()
        magic_links.send_link(link.pk)
        magic_links.send_link(link.pk)
        link.refresh_from_db()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(link.attempts, 1)
        self.assertIsNotNone(link.sent_at)
        self.assertIsNone(link.sending_at)

    def test_inflight_claim_refuses_a_second_worker(self):
        link = self.link()

        def while_sending(*args, **kwargs):
            magic_links.send_link(link.pk)
            return 1

        with patch.object(magic_links.EmailMultiAlternatives, "send", side_effect=while_sending) as send:
            magic_links.send_link(link.pk)
        self.assertEqual(send.call_count, 1)
        link.refresh_from_db()
        self.assertEqual(link.attempts, 1)

    def test_failed_delivery_waits_then_reconciles_with_only_three_attempts(self):
        link = self.link()
        with patch.object(magic_links.EmailMultiAlternatives, "send", side_effect=RuntimeError("private@example.org")) as send:
            for attempt in range(1, 4):
                with self.assertRaisesRegex(RuntimeError, "^auth_email_delivery_failed$"):
                    magic_links.send_link(link.pk)
                link.refresh_from_db()
                self.assertEqual(link.attempts, attempt)
                self.assertIsNone(link.sent_at)
                self.assertIsNone(link.sending_at)
                self.assertGreater(link.next_attempt_at, timezone.now())
                self.assertEqual(magic_links.reconcile_sign_in_links(), 0)
                magic_links.send_link(link.pk)
                self.assertEqual(send.call_count, attempt)
                EmailSignInLink.objects.filter(pk=link.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
                self.assertEqual(magic_links.reconcile_sign_in_links(), int(attempt < 3))
            magic_links.send_link(link.pk)
        self.assertEqual(send.call_count, 3)
        self.assertEqual(OrmQ.objects.count(), 2)
        for row in OrmQ.objects.all():
            package = SignedPackage.loads(row.payload)
            self.assertEqual(package["args"], (str(link.pk),))
            self.assertEqual(package["kwargs"], {})
            self.assertEqual(package["timeout"], 60)
            self.assertNotIn(link.email, repr(package))

    def test_stale_claim_is_recovered_and_live_claim_is_left_alone(self):
        link = self.link(attempts=1, sending_at=timezone.now())
        self.assertEqual(magic_links.reconcile_sign_in_links(), 0)
        EmailSignInLink.objects.filter(pk=link.pk).update(sending_at=timezone.now() - timedelta(seconds=61))
        self.assertEqual(magic_links.reconcile_sign_in_links(), 1)
        magic_links.send_link(link.pk)
        link.refresh_from_db()
        self.assertEqual(link.attempts, 2)
        self.assertIsNotNone(link.sent_at)

    def test_expired_consumed_or_changed_account_is_not_requeued(self):
        self.link(expires_at=timezone.now() - timedelta(seconds=1))
        self.link(consumed_at=timezone.now())
        user = make_user("Existing", email="new@example.org")
        self.link(purpose="login", user=user, email="old@example.org", original_email="old@example.org")
        self.assertEqual(magic_links.reconcile_sign_in_links(), 0)

    def test_console_and_file_backends_never_write_bearer_links(self):
        with tempfile.TemporaryDirectory() as directory:
            for connection in (ConsoleBackend(stream=io.StringIO()), FileBackend(file_path=directory)):
                with self.subTest(backend=type(connection).__name__):
                    link = self.link()
                    with patch.object(magic_links, "get_connection", return_value=connection), patch.object(
                        connection, "send_messages",
                    ) as send:
                        with self.assertRaisesRegex(RuntimeError, "^auth_email_delivery_failed$"):
                            magic_links.send_link(link.pk)
                    send.assert_not_called()
                    link.refresh_from_db()
                    self.assertIsNone(link.sent_at)

    def test_existing_recovery_command_includes_signup_without_a_user(self):
        link = self.link()
        call_command("reconcile_launch_emails", stdout=io.StringIO())
        self.assertEqual(OrmQ.objects.count(), 1)
        self.assertEqual(SignedPackage.loads(OrmQ.objects.get().payload)["args"], (str(link.pk),))

    def test_owner_scoped_recovery_excludes_other_and_pending_accounts(self):
        owner = make_user("Camille", email="owner@example.org")
        other = make_user("Other", email="other@example.org")
        own = self.link(purpose="login", user=owner, email=owner.email, original_email=owner.email)
        self.link(purpose="login", user=other, email=other.email, original_email=other.email)
        self.link()
        self.assertEqual(magic_links.reconcile_sign_in_links(owner.pk), 1)
        self.assertEqual(SignedPackage.loads(OrmQ.objects.get().payload)["args"], (str(own.pk),))
