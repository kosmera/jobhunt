"""The public page must not enter the private account flow."""

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.testing import make_user


class LandingTests(TestCase):
    @override_settings(AUTH_MODE="local")
    def test_anonymous_local_homepage_redirects_to_landing_with_any_profile_count(self):
        for profile_count in range(3):
            if profile_count:
                make_user(f"Profile {profile_count}")
            self.client.logout()
            with self.subTest(profile_count=profile_count):
                response = self.client.get("/", follow=True)
                self.assertRedirects(response, reverse("landing"))
                self.assertTemplateUsed(response, "landing.html")
                self.assertFalse(response.wsgi_request.user.is_authenticated)
                self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(AUTH_MODE="accounts")
    def test_anonymous_homepage_redirects_to_landing(self):
        make_user("Lionel")
        for signup_open in (True, False):
            with self.subTest(signup_open=signup_open), self.settings(SIGNUP_OPEN=signup_open):
                response = self.client.get("/", follow=True)
                self.assertRedirects(response, reverse("landing"))
                self.assertTemplateUsed(response, "landing.html")
                self.assertFalse(response.wsgi_request.user.is_authenticated)
                self.assertNotIn("_auth_user_id", self.client.session)

    def test_anonymous_htmx_homepage_redirects_to_landing(self):
        for auth_mode in ("local", "accounts"):
            with self.subTest(auth_mode=auth_mode), self.settings(AUTH_MODE=auth_mode):
                response = self.client.get("/", HTTP_HX_REQUEST="true")
                self.assertEqual(response.status_code, 204)
                self.assertEqual(response.headers["HX-Redirect"], reverse("landing"))

    @override_settings(AUTH_MODE="local")
    def test_public_local_page_without_creating_a_profile(self):
        response = self.client.get(reverse("landing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/bienvenue/"')
        self.assertContains(response, "Données fictives")
        self.assertNotIn("_auth_user_id", response.wsgi_request.session)

    @override_settings(AUTH_MODE="accounts", SIGNUP_OPEN=True)
    def test_account_mode_links_to_registration(self):
        response = self.client.get(reverse("landing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/inscription/"')

    @override_settings(AUTH_MODE="accounts", SIGNUP_OPEN=False)
    def test_closed_registration_links_to_sign_in(self):
        response = self.client.get(reverse("landing"))
        self.assertContains(response, "Accéder à mon espace")
        self.assertNotContains(response, 'href="/inscription/"')

    def test_landing_is_read_only(self):
        self.assertEqual(self.client.post(reverse("landing")).status_code, 405)
