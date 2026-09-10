"""The public page must not enter the private account flow."""

from django.test import TestCase, override_settings
from django.urls import reverse


class LandingTests(TestCase):
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
