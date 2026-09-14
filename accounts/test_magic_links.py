"""Production email sign-in: ownership proof, single-use links and password denial."""

from datetime import timedelta
import re
import smtplib
from unittest.mock import patch

from django.contrib import auth
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_q.models import OrmQ
from django_q.signing import SignedPackage

from accounts import magic_links
from accounts.models import EmailSignInLink, Profile
from accounts.onboarding import store
from accounts.onboarding.testing import walk
from accounts.testing import make_user


@override_settings(
    AUTH_MODE="accounts",
    PASSWORDLESS_AUTH=True,
    SIGNUP_OPEN=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    JOBHUNT_PUBLIC_URL="https://tonjobideal.com",
)
class MagicLinkTests(TestCase):
    def request_signup(self, **extra):
        return self.client.post(
            reverse("accounts:signup"),
            {"display_name": "Lionel", "email": "Lionel@Example.org", **extra},
        )

    def confirm_url(self, link=None):
        link = link or EmailSignInLink.objects.latest("created_at")
        return reverse("accounts:email_link_confirm", args=[magic_links.sign_link(link)])

    def verified_user(self, **kwargs):
        email = kwargs.pop("email", "lionel@example.org")
        return make_user(
            "Lionel", email=email, verified_email=email,
            email_verified_at=timezone.now(), **kwargs,
        )

    def test_signup_waits_for_email_proof_before_creating_an_account(self):
        response = self.request_signup()
        self.assertRedirects(response, reverse("accounts:email_link_sent"))
        self.assertFalse(User.objects.exists())
        self.assertNotIn("_auth_user_id", self.client.session)
        link = EmailSignInLink.objects.get()
        self.assertEqual(link.email, "lionel@example.org")
        self.assertEqual(link.display_name, "Lionel")
        self.assertIsNone(link.user_id)

        confirmed = self.client.post(self.confirm_url(link))
        self.assertRedirects(confirmed, reverse("accounts:onboarding"), fetch_redirect_response=False)
        user = User.objects.get()
        self.assertEqual(user.email, "lionel@example.org")
        self.assertFalse(user.has_usable_password())
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))
        profile = Profile.objects.get(user=user)
        self.assertEqual(profile.verified_email, user.email)
        self.assertIsNotNone(profile.email_verified_at)
        self.assertFalse(profile.is_onboarded)
        link.refresh_from_db()
        self.assertIsNotNone(link.consumed_at)

    def test_signup_and_login_forms_have_no_password_fields(self):
        for name in ("accounts:signup", "accounts:login"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'name="email"')
                self.assertNotContains(response, 'type="password"')

    def test_invalid_email_is_rejected_without_creating_a_link(self):
        response = self.request_signup(email="not an email")
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.assertFalse(EmailSignInLink.objects.exists())
        self.assertFalse(User.objects.exists())

    def test_scanner_get_and_head_do_not_consume_the_link(self):
        self.request_signup()
        link = EmailSignInLink.objects.get()
        url = self.confirm_url(link)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.head(url)
        link.refresh_from_db()
        self.assertIsNone(link.consumed_at)
        self.assertFalse(User.objects.exists())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_confirmation_requires_a_csrf_protected_post(self):
        self.request_signup()
        url = self.confirm_url()
        browser = self.client_class(enforce_csrf_checks=True)
        self.assertEqual(browser.get(url).status_code, 200)
        self.assertEqual(browser.post(url).status_code, 403)
        self.assertFalse(User.objects.exists())
        csrf_token = browser.cookies["csrftoken"].value
        self.assertEqual(browser.post(url, {"csrfmiddlewaretoken": csrf_token}).status_code, 302)
        self.assertEqual(User.objects.count(), 1)

    def test_link_is_single_use_even_in_a_new_browser(self):
        self.request_signup()
        url = self.confirm_url()
        self.assertEqual(self.client.post(url).status_code, 302)
        other_browser = self.client_class()
        response = other_browser.post(url)
        self.assertGreaterEqual(response.status_code, 400)
        self.assertNotIn("_auth_user_id", other_browser.session)
        self.assertEqual(User.objects.count(), 1)

    def test_expired_link_cannot_create_an_account(self):
        self.request_signup()
        link = EmailSignInLink.objects.get()
        url = self.confirm_url(link)
        EmailSignInLink.objects.filter(pk=link.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.client.post(url)
        self.assertGreaterEqual(response.status_code, 400)
        self.assertFalse(User.objects.exists())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_tampered_signature_cannot_create_an_account(self):
        self.request_signup()
        link = EmailSignInLink.objects.get()
        token = magic_links.sign_link(link)
        token = token[:-1] + ("a" if token[-1] != "a" else "b")
        response = self.client.post(reverse("accounts:email_link_confirm", args=[token]))
        self.assertGreaterEqual(response.status_code, 400)
        self.assertFalse(User.objects.exists())

    def test_queue_contains_only_identifiers_and_worker_delivers_a_link_once(self):
        self.request_signup()
        link = EmailSignInLink.objects.get()
        self.assertEqual(len(mail.outbox), 0)
        package = SignedPackage.loads(OrmQ.objects.get().payload)
        self.assertEqual(package["func"], "accounts.magic_links.send_link")
        self.assertNotIn(link.email, repr(package))
        self.assertNotIn(magic_links.sign_link(link), repr(package))
        magic_links.send_link(link.pk)
        magic_links.send_link(link.pk)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        assert isinstance(message, EmailMultiAlternatives)
        self.assertEqual(message.to, ["lionel@example.org"])
        match = re.search(r"https://tonjobideal\.com/\S+", str(message.body))
        assert match is not None
        url = match.group()
        confirmed_link = magic_links.read_link(url.rstrip("/").rsplit("/", 1)[-1])
        assert confirmed_link is not None
        self.assertEqual(confirmed_link.pk, link.pk)
        self.assertTrue(message.alternatives)
        link.refresh_from_db()
        self.assertIsNotNone(link.sent_at)

    def test_expired_link_is_not_delivered_by_a_late_worker(self):
        self.request_signup()
        link = EmailSignInLink.objects.get()
        EmailSignInLink.objects.filter(pk=link.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        magic_links.send_link(link.pk)
        self.assertEqual(len(mail.outbox), 0)

    def test_mail_failure_leaves_the_link_unsent_without_exposing_provider_details(self):
        self.request_signup()
        link = EmailSignInLink.objects.get()
        with patch(
            "accounts.magic_links.EmailMultiAlternatives.send",
            side_effect=smtplib.SMTPServerDisconnected("secret provider response for lionel@example.org"),
        ):
            with self.assertRaises(RuntimeError) as caught:
                magic_links.send_link(link.pk)
        self.assertEqual(str(caught.exception), "auth_email_delivery_failed")
        self.assertTrue(caught.exception.__suppress_context__)
        link.refresh_from_db()
        self.assertIsNone(link.sent_at)
        self.assertIsNone(link.consumed_at)

    def test_repeated_email_request_is_limited_across_browser_sessions(self):
        now = timezone.now()
        with patch("accounts.magic_links.timezone.now", return_value=now):
            first = self.request_signup()
            browser = self.client_class()
            repeated = browser.post(
                reverse("accounts:signup"), {"display_name": "Other", "email": "LIONEL@example.org"},
            )
        self.assertEqual(first["Location"], repeated["Location"])
        self.assertEqual(EmailSignInLink.objects.count(), 1)
        self.assertEqual(OrmQ.objects.count(), 1)

    def test_resend_after_cooldown_queues_a_new_link_and_creates_one_account(self):
        now = timezone.now()
        with patch("accounts.magic_links.timezone.now", return_value=now):
            self.request_signup()
        first_url = self.confirm_url()
        with patch("accounts.magic_links.timezone.now", return_value=now + timedelta(minutes=1)):
            self.client.post(reverse("accounts:email_link_resend"))
        self.assertEqual(EmailSignInLink.objects.count(), 2)
        second_url = self.confirm_url()
        self.assertEqual(self.client.post(second_url).status_code, 302)
        other = self.client_class()
        self.assertGreaterEqual(other.post(first_url).status_code, 400)
        self.assertEqual(User.objects.count(), 1)
        self.assertNotIn("_auth_user_id", other.session)

    def test_resend_preserves_signup_name_and_onboarding_snapshot(self):
        walk(self.client, until="identite")
        now = timezone.now()
        with patch("accounts.magic_links.timezone.now", return_value=now):
            self.client.post("/bienvenue/identite/", {"display_name": "Lionel", "email": "lionel@example.org"})
        initial = EmailSignInLink.objects.get()
        with patch("accounts.magic_links.timezone.now", return_value=now + timedelta(minutes=1)):
            response = self.client.post(
                reverse("accounts:email_link_resend"), {"email": "different@example.org", "display_name": "Different"},
            )
        self.assertRedirects(response, reverse("accounts:email_link_sent"))
        resent = EmailSignInLink.objects.exclude(pk=initial.pk).get()
        self.assertEqual(resent.email, initial.email)
        self.assertEqual(resent.display_name, initial.display_name)
        self.assertEqual(resent.onboarding_data, initial.onboarding_data)
        self.assertFalse(User.objects.exists())

    def test_resend_requires_a_csrf_protected_post(self):
        self.request_signup()
        url = reverse("accounts:email_link_resend")
        self.assertEqual(self.client.get(url).status_code, 405)
        browser = self.client_class(enforce_csrf_checks=True)
        self.assertEqual(browser.post(url).status_code, 403)
        self.assertEqual(EmailSignInLink.objects.count(), 1)

    def test_email_change_resend_requires_the_requesting_account(self):
        user = self.verified_user()
        self.client.force_login(user)
        now = timezone.now()
        with patch("accounts.magic_links.timezone.now", return_value=now):
            self.client.post(
                reverse("accounts:settings_section", args=["profil"]),
                {"display_name": "Lionel", "email": "new@example.org", "headline": "", "location": "", "phone": ""},
            )
        pending = self.client.session["pending_email_link"]
        self.client.logout()
        session = self.client.session
        session["pending_email_link"] = pending
        session.save()
        with patch("accounts.magic_links.timezone.now", return_value=now + timedelta(minutes=1)):
            response = self.client.post(reverse("accounts:email_link_resend"))
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertEqual(EmailSignInLink.objects.count(), 1)
        user.refresh_from_db()
        self.assertEqual(user.email, "lionel@example.org")

    def test_per_email_hourly_limit_survives_new_browser_sessions(self):
        # Keep all six requests in the same fixed hourly bucket.
        now = timezone.now().replace(minute=0, second=0, microsecond=0)
        for minute in range(6):
            with patch("accounts.magic_links.timezone.now", return_value=now + timedelta(minutes=minute)):
                response = self.client_class().post(
                    reverse("accounts:signup"), {"display_name": "Lionel", "email": "lionel@example.org"},
                )
                self.assertEqual(response.status_code, 302)
        self.assertEqual(EmailSignInLink.objects.count(), 5)
        self.assertEqual(OrmQ.objects.count(), 5)

    def test_unknown_addresses_count_towards_ip_limit(self):
        now = timezone.now()
        with patch("accounts.magic_links.timezone.now", return_value=now):
            for index in range(20):
                response = self.client.post(
                    reverse("accounts:login"), {"email": f"unknown-{index}@example.org"},
                )
                self.assertEqual(response.status_code, 302)
            response = self.request_signup()
        self.assertRedirects(response, reverse("accounts:email_link_sent"))
        self.assertFalse(EmailSignInLink.objects.exists())
        self.assertFalse(OrmQ.objects.exists())

    def test_signup_for_existing_email_cannot_replace_its_identity_or_answers(self):
        user = self.verified_user()
        self.request_signup(display_name="Attacker")
        link = EmailSignInLink.objects.get()
        self.assertEqual(link.purpose, "login")
        self.assertFalse(link.onboarding_data)
        self.assertEqual(self.client.post(self.confirm_url(link)).status_code, 302)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Profile.objects.get(user=user).display_name, "Lionel")

    def test_existing_account_login_normalizes_email_and_preserves_safe_destination(self):
        user = self.verified_user()
        response = self.client.post(
            reverse("accounts:login"), {"email": "  LIONEL@Example.org ", "next": "/pipeline/"},
        )
        self.assertRedirects(response, reverse("accounts:email_link_sent"))
        self.assertNotIn("_auth_user_id", self.client.session)
        confirmed = self.client.post(self.confirm_url())
        self.assertRedirects(confirmed, "/pipeline/")
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))
        self.assertEqual(User.objects.count(), 1)

    def test_external_redirect_is_discarded(self):
        self.verified_user()
        self.client.post(
            reverse("accounts:login"), {"email": "lionel@example.org", "next": "https://evil.example/"},
        )
        response = self.client.post(self.confirm_url())
        self.assertRedirects(response, reverse("tracker:dashboard"))

    def test_unknown_login_does_not_create_an_account_or_reveal_its_absence(self):
        response = self.client.post(reverse("accounts:login"), {"email": "unknown@example.org"})
        self.assertRedirects(response, reverse("accounts:email_link_sent"))
        self.assertFalse(User.objects.exists())
        self.assertFalse(EmailSignInLink.objects.exists())
        self.assertFalse(OrmQ.objects.exists())

    def test_deactivation_after_request_invalidates_login(self):
        user = self.verified_user()
        self.client.post(reverse("accounts:login"), {"email": user.email})
        url = self.confirm_url()
        User.objects.filter(pk=user.pk).update(is_active=False)
        self.assertGreaterEqual(self.client.post(url).status_code, 400)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_changed_email_after_request_invalidates_old_login_link(self):
        user = self.verified_user()
        self.client.post(reverse("accounts:login"), {"email": user.email})
        url = self.confirm_url()
        User.objects.filter(pk=user.pk).update(email="changed@example.org")
        self.assertGreaterEqual(self.client.post(url).status_code, 400)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_duplicate_legacy_email_cannot_select_an_arbitrary_account(self):
        self.verified_user(username="first")
        self.verified_user(username="second", email="LIONEL@example.org")
        response = self.client.post(reverse("accounts:login"), {"email": "lionel@example.org"})
        self.assertRedirects(response, reverse("accounts:email_link_sent"))
        self.assertFalse(EmailSignInLink.objects.exists())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_password_authentication_and_settings_password_post_are_disabled(self):
        user = self.verified_user()
        # Represent a pre-existing hash independently of production's save policy.
        User.objects.filter(pk=user.pk).update(password=make_password("legacy-password-42"))
        user.refresh_from_db()
        self.assertIsNone(auth.authenticate(username=user.username, password="legacy-password-42"))
        user.set_unusable_password()
        user.save()
        self.client.force_login(user)
        settings_page = self.client.get(reverse("accounts:settings"))
        self.assertNotContains(settings_page, 'type="password"')
        response = self.client.post(
            reverse("accounts:settings_section", args=["mot-de-passe"]),
            {"new_password1": "new-password-42", "new_password2": "new-password-42"},
        )
        self.assertGreaterEqual(response.status_code, 400)
        user.refresh_from_db()
        self.assertFalse(user.check_password("new-password-42"))

    def test_email_login_removes_a_historical_password_hash(self):
        user = self.verified_user()
        User.objects.filter(pk=user.pk).update(password=make_password("legacy-password-42"))
        self.client.post(reverse("accounts:login"), {"email": user.email})
        response = self.client.post(self.confirm_url())
        self.assertRedirects(response, reverse("tracker:dashboard"))
        user.refresh_from_db()
        self.assertFalse(user.has_usable_password())
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))

    def test_unverified_existing_session_cannot_open_private_pages(self):
        user = make_user("Unverified", email="unverified@example.org")
        self.client.force_login(user)
        response = self.client.get(reverse("tracker:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("landing"))

    def test_admin_login_uses_email_signin(self):
        for method in (self.client.get, self.client.post):
            with self.subTest(method=method.__name__):
                response = method(reverse("admin:login"))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("accounts:login"), response["Location"])

    def test_email_change_keeps_old_login_until_new_address_is_confirmed(self):
        user = self.verified_user()
        self.client.force_login(user)
        response = self.client.post(
            reverse("accounts:settings_section", args=["profil"]),
            {"display_name": "Lionel", "email": "New@Example.org", "headline": "", "location": "", "phone": ""},
        )
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertEqual(user.email, "lionel@example.org")
        link = EmailSignInLink.objects.get()
        self.assertEqual(link.email, "new@example.org")
        self.assertEqual(link.purpose, "change")
        self.assertEqual(self.client.post(self.confirm_url(link)).status_code, 302)
        user.refresh_from_db()
        self.assertEqual(user.email, "new@example.org")
        self.assertEqual(user.username, "new@example.org")
        profile = Profile.objects.get(user=user)
        self.assertEqual(profile.verified_email, "new@example.org")
        self.assertEqual(self.client.get(reverse("accounts:settings")).status_code, 200)

    def test_email_change_confirmation_requires_the_requesting_account(self):
        user = self.verified_user()
        self.client.force_login(user)
        self.client.post(
            reverse("accounts:settings_section", args=["profil"]),
            {"display_name": "Lionel", "email": "new@example.org", "headline": "", "location": "", "phone": ""},
        )
        link = EmailSignInLink.objects.get()
        other_browser = self.client_class()
        other_user = self.verified_user(username="other", email="other@example.org")
        other_browser.force_login(other_user)
        response = other_browser.post(self.confirm_url(link))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])
        link.refresh_from_db()
        user.refresh_from_db()
        self.assertIsNone(link.consumed_at)
        self.assertEqual(user.email, "lionel@example.org")

    def test_onboarding_answers_resume_after_confirmation_in_a_new_browser(self):
        walk(self.client, until="identite")
        response = self.client.post(
            "/bienvenue/identite/", {"display_name": "Lionel", "email": "lionel@example.org"},
        )
        self.assertRedirects(response, reverse("accounts:email_link_sent"))
        self.assertFalse(User.objects.exists())
        browser = self.client_class()
        self.assertEqual(browser.post(self.confirm_url()).status_code, 302)
        self.assertEqual(browser.get(reverse("accounts:onboarding"))["Location"], "/bienvenue/cv/")
        run = browser.session[store.SESSION_KEY]
        self.assertEqual(run["answers"]["cities"], ["Nivelles", "Wavre"])
        self.assertEqual(run["answers"]["display_name"], "Lionel")
        response = walk(browser)
        self.assertRedirects(response, reverse("tracker:dashboard"), fetch_redirect_response=False)
        self.assertTrue(Profile.objects.get(user=User.objects.get()).is_onboarded)
