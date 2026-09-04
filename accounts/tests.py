"""Tests for accounts: the two modes, onboarding, sign-in/sign-up, settings.

Mode is always pinned explicitly: the default derives from ``DEBUG``, which
the test runner forces to ``False`` after settings are loaded.
"""

from __future__ import annotations

import datetime as dt
import shutil
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import checks, conf
from accounts.models import Preferences, Profile
from accounts.services import LOCAL_USERNAME, profile_for, unique_username
from accounts.testing import OwnedTestCase, make_user
from tracker.models import Application, Company, Document, DocumentKind, Status

MEDIA = tempfile.mkdtemp(prefix="jobhunt-accounts-tests-")


def make_application(owner, title="Poste", company_name="Acme") -> Application:
    company, _ = Company.objects.get_or_create(owner=owner, name=company_name)
    return Application.objects.create(owner=owner, company=company, title=title)


# ---------------------------------------------------------------------------
# Local mode
# ---------------------------------------------------------------------------


@override_settings(AUTH_MODE="local")
class LocalModeTests(TestCase):
    def test_first_visit_lands_on_onboarding(self):
        response = self.client.get(reverse("tracker:dashboard"))
        self.assertRedirects(response, reverse("accounts:onboarding"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("accounts:onboarding")).status_code, 200)

    def test_onboarding_creates_a_password_less_profile_and_signs_in(self):
        response = self.client.post(
            reverse("accounts:onboarding"),
            {"display_name": "Lionel", "headline": "DevOps", "location": "Nivelles"},
        )
        self.assertRedirects(response, reverse("tracker:dashboard"))
        user = User.objects.get()
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.username, "lionel")
        profile = Profile.objects.get(user=user)
        self.assertEqual((profile.display_name, profile.location), ("Lionel", "Nivelles"))
        self.assertTrue(profile.is_onboarded)
        self.assertTrue(Preferences.objects.filter(user=user).exists())
        dashboard = self.client.get(reverse("tracker:dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.wsgi_request.user, user)
        self.assertContains(dashboard, "NIVELLES · RAYON 40 KM")

    def test_onboarding_requires_a_name(self):
        response = self.client.post(reverse("accounts:onboarding"), {"display_name": "   "})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.exists())

    def test_single_profile_is_signed_in_automatically(self):
        user = make_user("Lionel")
        response = self.client.get(reverse("tracker:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.user, user)
        self.assertContains(response, "Lionel")
        # One profile: nothing to switch to, so no sign-out control.
        self.assertNotContains(response, "Changer de profil")

    def test_placeholder_account_is_claimed_by_onboarding(self):
        """The ownership migration parks imported data on a nameless account;
        the first visit completes that account rather than creating another."""
        placeholder = User.objects.create_user(username=LOCAL_USERNAME, password=None)
        make_application(placeholder, "Ingénieur")
        make_application(placeholder, "SRE")

        response = self.client.get(reverse("tracker:dashboard"))
        self.assertRedirects(response, reverse("accounts:onboarding"), fetch_redirect_response=False)
        page = self.client.get(reverse("accounts:onboarding"))
        self.assertContains(page, "2 candidatures t'attendent")
        self.assertContains(page, "rattaché à ce profil.")

        response = self.client.post(reverse("accounts:onboarding"), {"display_name": "Lionel"})
        self.assertRedirects(response, reverse("tracker:dashboard"))
        self.assertEqual(User.objects.count(), 1)
        profile = Profile.objects.get(user=placeholder)
        self.assertTrue(profile.is_onboarded)
        self.assertEqual(profile.display_name, "Lionel")
        self.assertEqual(Application.objects.filter(owner=placeholder).count(), 2)

    def test_visiting_onboarding_directly_also_claims_the_single_account(self):
        placeholder = User.objects.create_user(username=LOCAL_USERNAME, password=None)
        self.client.post(reverse("accounts:onboarding"), {"display_name": "Lionel"})
        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(profile_for(placeholder).is_onboarded)

    def test_several_profiles_show_the_chooser(self):
        lionel = make_user("Lionel")
        marie = make_user("Marie")
        response = self.client.get(reverse("tracker:pipeline"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])
        chooser = self.client.get(reverse("accounts:login"))
        self.assertContains(chooser, "Lionel")
        self.assertContains(chooser, "Marie")
        self.assertContains(chooser, "Créer un autre profil")

        response = self.client.post(reverse("accounts:login"), {"user": marie.pk})
        self.assertRedirects(response, reverse("tracker:dashboard"))
        page = self.client.get(reverse("tracker:dashboard"))
        self.assertEqual(page.wsgi_request.user, marie)
        self.assertNotEqual(page.wsgi_request.user, lionel)
        # Two profiles: the rail offers to switch.
        self.assertContains(page, "Changer de profil")

    def test_chooser_rejects_unknown_or_inactive_profiles(self):
        make_user("Lionel")
        inactive = make_user("Ancien")
        inactive.is_active = False
        inactive.save()
        make_user("Marie")
        self.assertEqual(self.client.post(reverse("accounts:login"), {"user": "abc"}).status_code, 400)
        self.assertEqual(self.client.post(reverse("accounts:login"), {"user": inactive.pk}).status_code, 400)
        self.assertNotContains(self.client.get(reverse("accounts:login")), "Ancien")

    def test_switching_profile_goes_back_to_the_chooser(self):
        lionel = make_user("Lionel")
        make_user("Marie")
        self.client.force_login(lionel)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("accounts:login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_htmx_action_after_auto_sign_in_is_kept_and_the_page_refreshed(self):
        """Session gone while a page is open: the click still does its job,
        then the page reloads because signing in rotated the CSRF token."""
        user = make_user("Lionel")
        application = make_application(user)
        response = self.client.post(
            reverse("tracker:set_status", args=[application.pk]),
            {"status": Status.SENT, "source": "list"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["HX-Refresh"], "true")
        application.refresh_from_db()
        self.assertEqual(application.status, Status.SENT)
        # Once signed in, ordinary fragments carry no refresh order.
        again = self.client.get(reverse("tracker:stats_bar"), HTTP_HX_REQUEST="true")
        self.assertNotIn("HX-Refresh", again.headers)

    def test_htmx_fragment_with_several_profiles_redirects_to_the_chooser(self):
        make_user("Lionel")
        make_user("Marie")
        response = self.client.get(reverse("tracker:stats_bar"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 204)
        self.assertIn(reverse("accounts:login"), response.headers["HX-Redirect"])

    def test_account_without_profile_is_sent_to_onboarding_even_over_htmx(self):
        user = make_user("Sans profil", onboarded=False)
        self.client.force_login(user)
        response = self.client.get(reverse("tracker:dashboard"))
        self.assertRedirects(response, reverse("accounts:onboarding"), fetch_redirect_response=False)
        response = self.client.get(reverse("tracker:stats_bar"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["HX-Redirect"], reverse("accounts:onboarding"))

    def test_signup_is_not_a_thing_locally(self):
        response = self.client.get(reverse("accounts:signup"))
        self.assertRedirects(response, reverse("accounts:onboarding"), fetch_redirect_response=False)

    def test_onboarded_account_is_sent_away_from_onboarding(self):
        user = make_user("Lionel")
        self.client.force_login(user)
        response = self.client.get(reverse("accounts:onboarding"))
        self.assertRedirects(response, reverse("tracker:dashboard"))

    def test_admin_keeps_its_own_gate(self):
        make_user("Lionel")  # not staff
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response["Location"])


# ---------------------------------------------------------------------------
# Accounts mode
# ---------------------------------------------------------------------------


@override_settings(AUTH_MODE="accounts", SIGNUP_OPEN=True)
class AccountsModeTests(TestCase):
    PASSWORD = "un-mot-de-passe-solide-42"

    def test_anonymous_is_sent_to_login_with_next(self):
        make_user("Lionel")  # a single account must NOT be signed in automatically
        response = self.client.get(reverse("tracker:pipeline"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], f"{reverse('accounts:login')}?next={reverse('tracker:pipeline')}"
        )

    def test_htmx_anonymous_gets_a_redirect_header_not_a_login_page(self):
        response = self.client.get(
            reverse("tracker:stats_bar"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_CURRENT_URL="http://testserver/pipeline/?x=1",
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            response.headers["HX-Redirect"], f"{reverse('accounts:login')}?next=/pipeline/%3Fx%3D1"
        )

    def test_htmx_ignores_a_foreign_current_url(self):
        response = self.client.get(
            reverse("tracker:stats_bar"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_CURRENT_URL="https://evil.example/steal",
        )
        self.assertEqual(response.headers["HX-Redirect"], reverse("accounts:login"))

    def test_signup_creates_an_onboarded_account_and_signs_in(self):
        response = self.client.post(
            reverse("accounts:signup"),
            {
                "display_name": "Lionel",
                "email": "Lionel@Example.org",
                "password1": self.PASSWORD,
                "password2": self.PASSWORD,
            },
        )
        self.assertRedirects(response, reverse("tracker:dashboard"))
        user = User.objects.get()
        self.assertEqual(user.username, "lionel@example.org")
        self.assertEqual(user.email, "lionel@example.org")
        self.assertTrue(user.check_password(self.PASSWORD))
        self.assertTrue(profile_for(user).is_onboarded)
        self.assertEqual(self.client.get(reverse("tracker:dashboard")).wsgi_request.user, user)

    def test_signup_rejects_duplicates_and_weak_passwords(self):
        make_user("Lionel", username="lionel@example.org", email="lionel@example.org")
        response = self.client.post(
            reverse("accounts:signup"),
            {"display_name": "Bis", "email": "LIONEL@example.org",
             "password1": self.PASSWORD, "password2": self.PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)

        response = self.client.post(
            reverse("accounts:signup"),
            {"display_name": "Marie", "email": "marie@example.org",
             "password1": "1234", "password2": "1234"},
        )
        self.assertIn("password2", response.context["form"].errors)
        self.assertEqual(User.objects.count(), 1)

    def test_signup_can_be_closed(self):
        with override_settings(SIGNUP_OPEN=False):
            response = self.client.get(reverse("accounts:signup"))
            self.assertEqual(response.status_code, 403)
            self.assertContains(response, "inscriptions sont fermées", status_code=403)
            login = self.client.get(reverse("accounts:login"))
            self.assertNotContains(login, reverse("accounts:signup"))

    def test_login_with_email_in_any_case(self):
        user = make_user("Lionel", username="lionel@example.org", email="lionel@example.org",
                         password=self.PASSWORD)
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "  Lionel@Example.org ", "password": self.PASSWORD, "next": "/pipeline/"},
        )
        self.assertRedirects(response, "/pipeline/")
        self.assertEqual(self.client.get("/pipeline/").wsgi_request.user, user)

    def test_login_with_a_plain_username(self):
        make_user("Admin", username="admin", password=self.PASSWORD)
        response = self.client.post(
            reverse("accounts:login"), {"username": "admin", "password": self.PASSWORD}
        )
        self.assertRedirects(response, reverse("tracker:dashboard"))

    def test_login_with_the_email_of_a_locally_created_account(self):
        """The path the settings page promises: a local profile sets an e-mail
        and a password, then the instance switches to accounts mode."""
        user = make_user("Lionel", username="lionel", email="lionel@example.org",
                         password=self.PASSWORD)
        response = self.client.post(
            reverse("accounts:login"), {"username": "Lionel@Example.org", "password": self.PASSWORD}
        )
        self.assertRedirects(response, reverse("tracker:dashboard"))
        self.assertEqual(self.client.get(reverse("tracker:dashboard")).wsgi_request.user, user)

    def test_password_help_speaks_the_house_voice(self):
        response = self.client.get(reverse("accounts:signup"))
        self.assertContains(response, "Huit caractères ou plus")
        self.assertNotContains(response, "Votre mot de passe")

    def test_login_refuses_wrong_password_and_off_site_next(self):
        make_user("Lionel", username="lionel", password=self.PASSWORD)
        response = self.client.post(
            reverse("accounts:login"), {"username": "lionel", "password": "nope"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "incorrect")
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "lionel", "password": self.PASSWORD, "next": "https://evil.example/"},
        )
        self.assertRedirects(response, reverse("tracker:dashboard"))

    def test_password_less_chooser_does_not_work_in_accounts_mode(self):
        user = make_user("Lionel")
        response = self.client.post(reverse("accounts:login"), {"user": user.pk})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_is_post_only(self):
        user = make_user("Lionel")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("accounts:login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_anonymous_onboarding_is_signup(self):
        response = self.client.get(reverse("accounts:onboarding"))
        self.assertRedirects(response, reverse("accounts:signup"), fetch_redirect_response=False)

    def test_account_created_elsewhere_completes_its_profile_first(self):
        superuser = User.objects.create_superuser("root", "root@example.org", self.PASSWORD)
        self.client.force_login(superuser)
        response = self.client.get(reverse("tracker:dashboard"))
        self.assertRedirects(response, reverse("accounts:onboarding"), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("accounts:onboarding")), "Faisons connaissance")
        self.client.post(reverse("accounts:onboarding"), {"display_name": "Root"})
        self.assertTrue(profile_for(superuser).is_onboarded)
        self.assertEqual(self.client.get(reverse("tracker:dashboard")).status_code, 200)

    def test_rail_shows_sign_out(self):
        self.client.force_login(make_user("Lionel"))
        response = self.client.get(reverse("tracker:dashboard"))
        self.assertContains(response, "Se déconnecter")
        self.assertNotContains(response, "Changer de profil")


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------


@override_settings(MEDIA_ROOT=MEDIA)
class SettingsTests(OwnedTestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def post(self, section, **data):
        return self.client.post(reverse("accounts:settings_section", args=[section]), data)

    def test_page_renders_with_every_section(self):
        response = self.client.get(reverse("accounts:settings"))
        self.assertEqual(response.status_code, 200)
        for anchor in ("profil", "preferences", "mot-de-passe", "supprimer"):
            self.assertContains(response, f'id="{anchor}"')
        self.assertContains(response, "Définir un mot de passe")  # no usable password yet
        self.assertContains(response, "facultatif en local")
        self.assertNotContains(response, "Votre mot de passe")

    def test_section_urls(self):
        self.assertEqual(self.client.get(reverse("accounts:settings_section", args=["bogus"])).status_code, 404)
        response = self.client.get(reverse("accounts:settings_section", args=["profil"]))
        self.assertRedirects(response, reverse("accounts:settings"))

    def test_profile_update(self):
        response = self.post(
            "profil", display_name="Lionel D.", headline="SRE", location="Braine", email="L@Example.org"
        )
        self.assertRedirects(response, reverse("accounts:settings"))
        self.user.refresh_from_db()
        profile = profile_for(self.user)
        self.assertEqual((profile.display_name, profile.headline, profile.location), ("Lionel D.", "SRE", "Braine"))
        self.assertEqual(self.user.email, "l@example.org")
        self.assertEqual(self.user.username, "lionel")  # a slug username is left alone
        page = self.client.get(reverse("tracker:dashboard"))
        self.assertContains(page, "BRAINE · RAYON 40 KM")
        self.assertContains(page, "Lionel D.")

    def test_phone_is_optional_and_kept(self):
        """Facultatif : le profil s'enregistre sans, et le retient quand il y en a."""
        self.post("profil", display_name="Lionel", email="", phone="")
        self.assertEqual(profile_for(self.user).phone, "")
        self.post("profil", display_name="Lionel", email="", phone=" +32 470 12 34 56 ")
        self.assertEqual(profile_for(self.user).phone, "+32 470 12 34 56")
        page = self.client.get(reverse("accounts:settings"))
        self.assertContains(page, "+32 470 12 34 56")

    def test_email_change_keeps_an_email_username_in_step(self):
        user = make_user("Marie", username="marie@example.org", email="marie@example.org")
        self.client.force_login(user)
        self.post("profil", display_name="Marie", email="marie@new.org")
        user.refresh_from_db()
        self.assertEqual(user.username, "marie@new.org")

    def test_email_must_be_unique(self):
        make_user("Marie", username="marie@example.org", email="marie@example.org")
        response = self.post("profil", display_name="Lionel", email="MARIE@example.org")
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["profile_form"].errors)

    def test_preferences_update_and_apply(self):
        response = self.post(
            "preferences", stale_after_days=5, follow_up_days=3, search_radius_km=60,
            default_cv_language="en",
        )
        self.assertRedirects(response, reverse("accounts:settings"))
        preferences = Preferences.objects.get(user=self.user)
        self.assertEqual(preferences.stale_after_days, 5)
        self.assertEqual(preferences.follow_up_days, 3)
        # A follow-up sent six days ago is now stale (threshold 5, default 14).
        application = make_application(self.user)
        application.status = Status.SENT
        application.applied_on = timezone.localdate() - dt.timedelta(days=6)
        application.save()
        dashboard = self.client.get(reverse("tracker:dashboard"))
        self.assertContains(dashboard, "RAYON 60 KM")
        self.assertContains(dashboard, "plus de 5 jours")
        self.assertIn(application, dashboard.context["attention"])
        # The quick-add form defaults to the preferred CV language.
        fragment = self.client.get(reverse("tracker:quick_create"))
        self.assertContains(fragment, '<option value="en" selected>')

    def test_preferences_are_bounded(self):
        response = self.post("preferences", stale_after_days=0, follow_up_days=3,
                             search_radius_km=60, default_cv_language="fr")
        self.assertIn("stale_after_days", response.context["preferences_form"].errors)

    def test_set_then_change_password(self):
        self.assertFalse(self.user.has_usable_password())
        response = self.post("mot-de-passe", new_password1="un-mot-de-passe-solide-42",
                             new_password2="un-mot-de-passe-solide-42")
        self.assertRedirects(response, reverse("accounts:settings"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("un-mot-de-passe-solide-42"))
        # The session survived the hash change.
        self.assertEqual(self.client.get(reverse("accounts:settings")).status_code, 200)
        self.assertContains(self.client.get(reverse("accounts:settings")), "Changer le mot de passe")

        response = self.post("mot-de-passe", old_password="nope",
                             new_password1="autre-mot-de-passe-99", new_password2="autre-mot-de-passe-99")
        self.assertIn("old_password", response.context["password_form"].errors)
        response = self.post("mot-de-passe", old_password="un-mot-de-passe-solide-42",
                             new_password1="autre-mot-de-passe-99", new_password2="autre-mot-de-passe-99")
        self.assertRedirects(response, reverse("accounts:settings"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("autre-mot-de-passe-99"))

    def test_delete_account_needs_the_exact_name(self):
        make_application(self.user)
        response = self.post("supprimer", confirm="quelqu'un d'autre")
        self.assertEqual(response.status_code, 200)
        self.assertIn("confirm", response.context["delete_form"].errors)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_delete_account_takes_everything_with_it(self):
        other = make_user("Marie")
        make_application(other, "Reste", company_name="Widgets")
        application = make_application(self.user)
        document = Document.objects.create(
            owner=self.user, application=application, kind=DocumentKind.CV, label="CV",
            file=SimpleUploadedFile("cv.docx", b"x"),
        )
        library = Document.objects.create(
            owner=self.user, kind=DocumentKind.CV, label="Base", file=SimpleUploadedFile("base.docx", b"y"),
        )
        paths = [Path(document.file.path), Path(library.file.path)]

        # The files go with the rows, but only once the deletion is committed
        # (``tracker.models.delete_file_from_storage``).
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post("supprimer", confirm=" lionel ")
        self.assertRedirects(response, reverse("accounts:login"), fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(Application.objects.filter(owner_id=self.user.pk).exists())
        self.assertFalse(Company.objects.filter(owner_id=self.user.pk).exists())
        self.assertFalse(Document.objects.filter(owner_id=self.user.pk).exists())
        for path in paths:
            self.assertFalse(path.exists())
        # The other profile is untouched, and (local mode, one profile left) signed in next.
        self.assertEqual(Application.objects.filter(owner=other).count(), 1)
        self.assertEqual(self.client.get(reverse("tracker:dashboard")).wsgi_request.user, other)


# ---------------------------------------------------------------------------
# Small parts
# ---------------------------------------------------------------------------


class ServiceTests(TestCase):
    def test_unique_username(self):
        self.assertEqual(unique_username("Lionel Dupont"), "lionel-dupont")
        make_user("Lionel Dupont")
        self.assertEqual(unique_username("Lionel Dupont"), "lionel-dupont-2")
        self.assertEqual(unique_username("!!!"), "profil")

    def test_initials(self):
        user = make_user("Lionel Dupont")
        self.assertEqual(profile_for(user).initials, "LD")
        user = make_user("Marie")
        self.assertEqual(profile_for(user).initials, "MA")
        user = make_user("", username="root")
        self.assertEqual(profile_for(user).initials, "RO")

    def test_profile_rows_follow_user_creation(self):
        user = User.objects.create_user("x", password=None)
        self.assertTrue(Profile.objects.filter(user=user).exists())
        self.assertTrue(Preferences.objects.filter(user=user).exists())

    def test_new_preferences_take_the_environment_defaults(self):
        with override_settings(STALE_AFTER_DAYS=21, DEFAULT_FOLLOW_UP_DAYS=7, DEFAULT_SEARCH_RADIUS_KM=25):
            preferences = Preferences.objects.get(user=make_user("Lionel"))
        self.assertEqual(
            (preferences.stale_after_days, preferences.follow_up_days, preferences.search_radius_km),
            (21, 7, 25),
        )

    def test_mode_helpers(self):
        with override_settings(AUTH_MODE="local", SIGNUP_OPEN=True):
            self.assertTrue(conf.is_local())
            self.assertFalse(conf.signup_open())
        with override_settings(AUTH_MODE="accounts", SIGNUP_OPEN=True):
            self.assertTrue(conf.signup_open())


class CheckTests(TestCase):
    def test_invalid_mode_is_an_error(self):
        with override_settings(AUTH_MODE="bogus"):
            ids = [problem.id for problem in checks.check_auth_mode(None)]
        self.assertEqual(ids, ["accounts.E001"])

    def test_insecure_key_in_accounts_mode_is_a_warning(self):
        with override_settings(AUTH_MODE="accounts", SECRET_KEY="django-insecure-x"):
            ids = [problem.id for problem in checks.check_auth_mode(None)]
        self.assertEqual(ids, ["accounts.W001"])
        with override_settings(AUTH_MODE="local", SECRET_KEY="django-insecure-x"):
            self.assertEqual(checks.check_auth_mode(None), [])

    def test_accounts_mode_behind_tls_needs_an_https_origin(self):
        secure = dict(AUTH_MODE="accounts", DEBUG=False, SECRET_KEY="k", SECURE_PROXY_SSL_HEADER=None)
        with override_settings(**secure, CSRF_TRUSTED_ORIGINS=["http://localhost:8000"]):
            ids = [problem.id for problem in checks.check_tls_origin(None)]
        self.assertEqual(ids, ["accounts.W002"])
        with override_settings(**secure, CSRF_TRUSTED_ORIGINS=["https://jobhunt.example"]):
            self.assertEqual(checks.check_tls_origin(None), [])
        with override_settings(
            **{**secure, "SECURE_PROXY_SSL_HEADER": ("HTTP_X_FORWARDED_PROTO", "https")},
            CSRF_TRUSTED_ORIGINS=["http://localhost:8000"],
        ):
            self.assertEqual(checks.check_tls_origin(None), [])
        with override_settings(**{**secure, "AUTH_MODE": "local"}, CSRF_TRUSTED_ORIGINS=[]):
            self.assertEqual(checks.check_tls_origin(None), [])


class ApplicationStatusSmokeTests(OwnedTestCase):
    """The tracker still works end to end for a signed-in profile."""

    def test_quick_add_and_detail(self):
        response = self.client.post(
            reverse("tracker:quick_create"),
            {"company_name": "Acme", "title": "Poste", "cv_language": "fr", "status": Status.BACKLOG},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 204)
        application = Application.objects.get()
        self.assertEqual(application.owner, self.user)
        self.assertEqual(application.company.owner, self.user)
        self.assertEqual(self.client.get(application.get_absolute_url()).status_code, 200)
