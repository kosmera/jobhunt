"""The public post-CV screen captures intent without buying or granting access."""

from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Profile
from accounts.onboarding import store
from accounts.onboarding.testing import docx_bytes, walk
from accounts.services import has_premium, profile_for
from accounts.testing import make_user
from tracker.models import Document


def step(slug):
    return reverse("accounts:onboarding_step", args=[slug])


@override_settings(AUTH_MODE="accounts", SIGNUP_OPEN=True, IS_SAAS_PRODUCTION=True, LAUNCH_INTEREST_ENABLED=True)
@mock.patch("accounts.onboarding.services.cv_analyzer", new=lambda: None)
class LaunchInterestFlowTests(TestCase):
    def setUp(self):
        self.user = make_user("Camille", email="camille@example.org", onboarded=False)
        self.client.force_login(self.user)

    def interest(self, **overrides):
        return {
            "action": "continue", "email": "camille@example.org",
            "plan": "premium", "consent": "on", **overrides,
        }

    def test_cv_upload_leads_to_pricing_then_saves_interest_and_keeps_cv(self):
        walk(self.client, until="cv")
        response = self.client.post(step("cv"), {
            "action": "upload", "language": "fr",
            "file": SimpleUploadedFile("cv.docx", docx_bytes()),
        })
        self.assertRedirects(response, step("cv"), fetch_redirect_response=False)
        document = Document.objects.get(owner=self.user)
        response = self.client.post(step("cv"), {"action": "continue"})
        self.assertRedirects(response, step("plan"), fetch_redirect_response=False)
        page = self.client.get(step("plan"))
        self.assertTemplateUsed(page, "accounts/onboarding/interest.html")
        self.assertEqual(page.context["form"]["email"].value(), self.user.email)
        self.assertFalse(page.context["form"]["consent"].value())
        self.assertFalse(page.context["form"]["plan"].value())
        self.assertFalse(profile_for(self.user).is_onboarded)

        response = self.client.post(step("plan"), self.interest(email="news@example.org"), follow=True)
        self.assertRedirects(response, reverse("tracker:dashboard"))
        profile = profile_for(self.user)
        self.assertTrue(profile.is_onboarded)
        self.assertEqual(profile.launch_email, "news@example.org")
        self.assertEqual(profile.launch_plan, "premium")
        self.assertIsNotNone(profile.launch_consent_at)
        self.assertFalse(has_premium(self.user))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "camille@example.org")
        self.assertTrue(Document.objects.filter(pk=document.pk, owner=self.user).exists())
        self.assertNotIn(store.SESSION_KEY, self.client.session)
        self.assertContains(response, "Ton intérêt est enregistré")

    def test_skip_opens_workspace_without_opt_in_even_with_forged_interest_fields(self):
        walk(self.client, until="plan")
        response = self.client.post(step("plan"), self.interest(action="skip"))
        self.assertRedirects(response, reverse("tracker:dashboard"), fetch_redirect_response=False)
        profile = profile_for(self.user)
        self.assertTrue(profile.is_onboarded)
        self.assertEqual((profile.launch_email, profile.launch_plan), ("", ""))
        self.assertIsNone(profile.launch_consent_at)
        self.assertFalse(has_premium(self.user))

    def test_invalid_form_preserves_inputs_and_never_completes_or_opts_in(self):
        walk(self.client, until="plan")
        for fields, error_field in (
            ({"email": ""}, "email"),
            ({"email": "invalid"}, "email"),
            ({"email": "a" * 255 + "@example.org"}, "email"),
            ({"plan": ""}, "plan"),
            ({"plan": "administrator"}, "plan"),
            ({"consent": ""}, "consent"),
        ):
            with self.subTest(fields=fields):
                response = self.client.post(step("plan"), self.interest(**fields))
                self.assertEqual(response.status_code, 200)
                self.assertIn(error_field, response.context["form"].errors)
                self.assertEqual(response.context["form"]["email"].value(), fields.get("email", self.user.email))
                self.assertFalse(profile_for(self.user).is_onboarded)
                self.assertIsNone(profile_for(self.user).launch_consent_at)
        response = self.client.post(step("plan"), self.interest(plan="free"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(profile_for(self.user).launch_plan, "free")

    def test_get_and_repeated_completed_post_do_not_record_or_change_interest(self):
        walk(self.client, until="plan")
        self.client.get(step("plan"), self.interest())
        self.assertIsNone(profile_for(self.user).launch_consent_at)
        self.client.post(step("plan"), self.interest())
        first = profile_for(self.user)
        response = self.client.post(step("plan"), self.interest(email="different@example.org", plan="free"))
        self.assertRedirects(response, reverse("tracker:dashboard"), fetch_redirect_response=False)
        after = profile_for(self.user)
        self.assertEqual(after.launch_email, first.launch_email)
        self.assertEqual(after.launch_plan, first.launch_plan)
        self.assertEqual(after.launch_consent_at, first.launch_consent_at)

    def test_cannot_submit_interest_before_finishing_cv_step(self):
        walk(self.client, until="cv")
        response = self.client.post(step("plan"), self.interest())
        self.assertRedirects(response, step("cv"), fetch_redirect_response=False)
        self.assertFalse(profile_for(self.user).is_onboarded)
        self.assertIsNone(profile_for(self.user).launch_consent_at)

    def test_expired_and_anonymous_requests_do_not_record_interest(self):
        response = self.client.post(step("plan"), self.interest())
        self.assertRedirects(response, step("situation"), fetch_redirect_response=False)
        self.client.logout()
        response = self.client.post(step("plan"), self.interest())
        self.assertRedirects(response, step("situation"), fetch_redirect_response=False)
        self.assertFalse(Profile.objects.filter(launch_consent_at__isnull=False).exists())

    def test_request_cannot_capture_interest_for_another_account(self):
        other = make_user("Autre", email="other@example.org", onboarded=False)
        walk(self.client, until="plan")
        self.client.post(step("plan"), self.interest(user_id=other.pk, subscription_level="premium"))
        self.assertIsNone(profile_for(other).launch_consent_at)
        self.assertFalse(profile_for(other).is_onboarded)
        self.assertIsNotNone(profile_for(self.user).launch_consent_at)
        self.assertFalse(has_premium(self.user))

    def test_csrf_is_required_to_capture_interest(self):
        walk(self.client, until="plan")
        strict = self.client_class(enforce_csrf_checks=True)
        strict.cookies = self.client.cookies
        response = strict.post(step("plan"), self.interest())
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(profile_for(self.user).launch_consent_at)

    @override_settings(AUTH_MODE="local", IS_SAAS_PRODUCTION=False)
    def test_local_installation_keeps_its_existing_plan_and_access(self):
        page = walk(self.client, until="plan")
        self.assertTemplateUsed(page, "accounts/onboarding/plan.html")
        self.assertNotContains(page, 'name="consent"')
        self.client.post(step("plan"), {"action": "continue"})
        self.assertTrue(profile_for(self.user).is_onboarded)
        self.assertTrue(has_premium(self.user))
        self.assertIsNone(profile_for(self.user).launch_consent_at)

    @override_settings(AUTH_MODE="accounts", IS_SAAS_PRODUCTION=False, LAUNCH_INTEREST_ENABLED=False)
    def test_development_accounts_mode_does_not_collect_launch_interest(self):
        page = walk(self.client, until="plan")
        self.assertTemplateUsed(page, "accounts/onboarding/plan.html")
        self.assertNotContains(page, 'name="consent"')
        self.client.post(step("plan"), self.interest())
        self.assertTrue(profile_for(self.user).is_onboarded)
        self.assertIsNone(profile_for(self.user).launch_consent_at)

    @override_settings(IS_SAAS_PRODUCTION=False)
    def test_production_collector_does_not_require_paid_saas_features(self):
        page = walk(self.client, until="plan")
        self.assertTemplateUsed(page, "accounts/onboarding/interest.html")
        self.client.post(step("plan"), self.interest())
        self.assertIsNotNone(profile_for(self.user).launch_consent_at)
        self.assertFalse(has_premium(self.user))
