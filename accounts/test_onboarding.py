"""The questionnaire on the web: both modes end to end, the gate, the CV, the services.

The copilot may or may not be installed where the suite runs: every
test pins what ``cv_analyzer()`` answers so the projected path is the same
here and in CI.
"""

from __future__ import annotations

import re
from unittest import mock, skipUnless

from django.apps import apps
from django.contrib.auth.models import User
from django.db import ProgrammingError, connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import EmailSignInLink, LaunchEmailJob, Preferences, Profile, SearchProfile
from accounts.onboarding import store
from accounts.onboarding.flow import machine
from accounts.onboarding.machine import Run
from accounts.onboarding.services import format_euros
from accounts.onboarding.testing import DEFAULT_ANSWERS, DEFAULT_POSTS, all_answers, walk
from accounts.services import (
    LOCAL_USERNAME,
    finish_onboarding,
    has_premium,
    name_profile,
    preferences_for,
    profile_for,
)
from accounts.testing import make_user
from rls import as_user
from rls.testing import app_role
from tracker.models import Application, Company, Document

ENTRY = reverse("accounts:onboarding")


def url(slug: str) -> str:
    return reverse("accounts:onboarding_step", args=[slug])


def make_application(owner, title="Poste", company_name="Acme") -> Application:
    company, _ = Company.objects.get_or_create(owner=owner, name=company_name)
    return Application.objects.create(owner=owner, company=company, title=title)


#: ``new=`` so that the class decorator injects no extra argument into the tests.
NO_PLUGIN = mock.patch("accounts.onboarding.services.cv_analyzer", new=lambda: None)


@override_settings(AUTH_MODE="local")
@NO_PLUGIN
class LocalFlowTests(TestCase):
    def test_first_visit_offers_onboarding_from_the_landing_page(self):
        response = self.client.get(reverse("tracker:dashboard"), follow=True)
        self.assertRedirects(response, reverse("landing"))
        self.assertContains(response, f'href="{ENTRY}"')
        response = self.client.get(ENTRY)
        self.assertRedirects(response, url("situation"), fetch_redirect_response=False)
        page = self.client.get(url("situation"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "1/19")
        self.assertContains(page, "Où en es-tu aujourd")
        self.assertNotIn(store.SESSION_KEY, self.client.session)

    def test_a_future_step_redirects_to_the_current_one(self):
        response = self.client.get(url("postes"))
        self.assertRedirects(response, url("situation"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(url("inconnue")).status_code, 404)

    def test_answers_advance_and_persist(self):
        response = self.client.post(url("situation"), {"choice": "employed_open"})
        self.assertRedirects(response, url("rythme"), fetch_redirect_response=False)
        run = store.load(mock.Mock(session=self.client.session))
        assert run is not None
        self.assertEqual(run.state, "intro_pace")
        self.assertEqual(run.answers, {"employment_status": "employed_open"})
        again = self.client.get(url("situation"))
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.context["form"]["choice"].value(), "employed_open")
        self.assertNotContains(again, "Revenir à l'étape précédente")
        next_page = self.client.get(url("rythme"))
        self.assertContains(next_page, f'href="{url("situation")}"')

    def test_invalid_answer_re_renders_with_an_error(self):
        page = walk(self.client, until="aide")
        self.assertEqual(page.status_code, 200)
        response = self.client.post(url("aide"), {})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choisis au moins une réponse.")
        run = store.load(mock.Mock(session=self.client.session))
        assert run is not None
        self.assertEqual(run.state, "help")

    def test_auto_advance_steps_still_render_a_continue_button(self):
        page = walk(self.client, until="experience")
        self.assertContains(page, "data-auto-advance")
        self.assertContains(page, 'value="continue"')

    def test_full_walk_creates_the_account_and_the_rows(self):
        page = walk(self.client, until="cv")
        self.assertEqual(page.status_code, 200)
        user = User.objects.get()
        self.assertEqual(user.username, "lionel")
        self.assertFalse(user.has_usable_password())
        self.assertEqual(page.wsgi_request.user, user)
        self.assertFalse(profile_for(user).is_onboarded)

        response = walk(self.client)
        self.assertRedirects(response, reverse("tracker:dashboard"), fetch_redirect_response=False)
        profile = Profile.objects.get(user=user)
        self.assertTrue(profile.is_onboarded)
        self.assertEqual(profile.display_name, "Lionel")
        self.assertEqual(profile.headline, "Ingénieur DevOps")
        self.assertEqual(profile.location, "Nivelles")
        preferences = Preferences.objects.get(user=user)
        self.assertEqual(preferences.search_radius_km, 40)
        self.assertEqual(preferences.default_cv_language, "fr")
        search = SearchProfile.objects.get(user=user)
        self.assertEqual(search.job_titles, ["Ingénieur DevOps", "Ingénieur cloud"])
        self.assertEqual(search.industries, ["it", "consulting"])
        self.assertEqual(search.work_types, ["permanent", "freelance"])
        self.assertEqual((search.work_mode, search.cities), ("hybrid", ["Nivelles", "Wavre"]))
        self.assertEqual((search.salary_min, search.salary_period), (5900, "month"))
        self.assertEqual(search.start_timeline, "asap")
        self.assertEqual(search.help_wanted, ["track", "documents"])
        self.assertNotIn(store.SESSION_KEY, self.client.session)
        dashboard = self.client.get(reverse("tracker:dashboard"))
        self.assertContains(dashboard, "Bienvenue, Lionel.")
        self.assertContains(dashboard, "NIVELLES · RAYON 40 KM")

    def test_identity_keeps_the_answers_across_sign_in(self):
        walk(self.client, until="identite")
        before = store.load(mock.Mock(session=self.client.session))
        assert before is not None
        self.client.post(url("identite"), {"display_name": "Lionel"})
        after = store.load(mock.Mock(session=self.client.session))
        assert after is not None
        self.assertEqual(dict(after.answers), {**before.answers, "display_name": "Lionel"})
        self.assertEqual(after.state, "cv")

    def test_identity_requires_a_name(self):
        walk(self.client, until="identite")
        response = self.client.post(url("identite"), {"display_name": "   "})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Il faut bien un nom.")
        self.assertFalse(User.objects.exists())

    def test_placeholder_account_starts_with_welcome_back(self):
        placeholder = User.objects.create_user(username=LOCAL_USERNAME, password=None)
        make_application(placeholder, "Ingénieur")
        make_application(placeholder, "SRE")
        response = self.client.get(reverse("tracker:pipeline"))
        self.assertRedirects(response, ENTRY, fetch_redirect_response=False)
        response = self.client.get(ENTRY)
        self.assertRedirects(response, url("reprise"), fetch_redirect_response=False)
        page = self.client.get(url("reprise"))
        self.assertContains(page, "2 candidatures t'attendent")
        self.assertContains(page, "rattaché à ce profil")
        self.assertNotContains(page, 'role="progressbar"')
        gate = walk(self.client, until="identite")
        self.assertContains(gate, "Il ne manque qu'un nom")
        walk(self.client)
        self.assertEqual(User.objects.count(), 1)
        profile = Profile.objects.get(user=placeholder)
        self.assertTrue(profile.is_onboarded)
        self.assertEqual(profile.display_name, "Lionel")
        self.assertEqual(Application.objects.filter(owner=placeholder).count(), 2)

    def test_remote_skips_cities_and_counts_eighteen(self):
        page = walk(self.client, until="salaire", posts={"work_mode": {"choice": "remote"}})
        self.assertContains(page, "13/18")
        self.assertRedirects(self.client.get(url("villes")), url("salaire"), fetch_redirect_response=False)
        review = walk(self.client, until="bilan", posts={"work_mode": {"choice": "remote"}})
        self.assertNotContains(review, "Villes et rayon")
        self.assertContains(review, "Télétravail")

    def test_skip_industries_and_salary_then_plan_has_no_cost_line(self):
        posts = {"industries": {"action": "skip"}, "salary": {"action": "skip"}}
        review = walk(self.client, until="bilan", posts=posts)
        self.assertEqual(review.content.decode().count("— passé"), 2)
        plan = walk(self.client, until="plan", posts=posts)
        self.assertNotContains(plan, "de salaire brut")
        self.assertContains(plan, "Une lettre de motivation")

    def test_plan_shows_the_weekly_cost_from_the_visitor_s_own_salary(self):
        plan = walk(self.client, until="plan")
        self.assertContains(plan, format_euros(1362))

    def test_urgent_status_shortens_the_follow_up_delay(self):
        walk(self.client, posts={"status": {"choice": "unemployed_urgent"}})
        self.assertEqual(Preferences.objects.get().follow_up_days, 7)

    def test_review_edit_returns_to_review(self):
        walk(self.client, until="bilan")
        response = self.client.post(url("bilan"), {"action": "edit", "target": "experience"})
        self.assertRedirects(response, url("experience"), fetch_redirect_response=False)
        page = self.client.get(url("experience"))
        self.assertContains(page, "Enregistrer et revenir au bilan")
        self.assertContains(page, f'href="{url("bilan")}"')
        response = self.client.post(url("experience"), {"choice": "senior"})
        self.assertRedirects(response, url("bilan"), fetch_redirect_response=False)
        self.assertContains(self.client.get(url("bilan")), "Senior")

    def test_review_edit_detours_through_a_newly_enabled_step(self):
        walk(self.client, until="bilan", posts={"work_mode": {"choice": "remote"}})
        self.client.post(url("bilan"), {"action": "edit", "target": "mode"})
        response = self.client.post(url("mode"), {"choice": "hybrid"})
        self.assertRedirects(response, url("villes"), fetch_redirect_response=False)
        response = self.client.post(url("villes"), {"cities": ["Mons"], "search_radius_km": "20"})
        self.assertRedirects(response, url("bilan"), fetch_redirect_response=False)
        self.assertContains(self.client.get(url("bilan")), "Mons · 20 km")

    def test_edit_from_an_unvisited_target_is_refused(self):
        walk(self.client, until="bilan")
        response = self.client.post(url("bilan"), {"action": "edit", "target": "cv"}, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], url("bilan"))
        self.assertContains(response, "Reprends ici")

    def test_post_from_a_stale_tab_is_refused(self):
        walk(self.client, until="salaire")
        response = self.client.post(url("horizon"), {"choice": "asap"}, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], url("salaire"))
        self.assertContains(response, "Reprends ici")

    def test_post_without_a_session_restarts_with_a_message(self):
        response = self.client.post(url("horizon"), {"choice": "asap"}, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], url("situation"))
        self.assertContains(response, "Ta session a expiré")

    def test_onboarded_account_is_sent_away_from_any_step(self):
        user = make_user("Lionel")
        self.client.force_login(user)
        session = self.client.session
        session[store.SESSION_KEY] = Run(state="cv").to_json()
        session.save()
        response = self.client.get(url("cv"))
        self.assertRedirects(response, reverse("tracker:dashboard"), fetch_redirect_response=False)
        self.assertNotIn(store.SESSION_KEY, self.client.session)

    def test_a_run_past_the_gate_goes_back_to_the_gate_when_the_account_is_gone(self):
        walk(self.client, until="plan")
        User.objects.update(is_active=False)
        response = self.client.get(ENTRY)
        self.assertRedirects(response, url("identite"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(url("identite")).status_code, 200)
        self.assertRedirects(self.client.get(url("plan")), url("identite"), fetch_redirect_response=False)
        response = self.client.post(url("identite"), {"display_name": "Lionel"})
        self.assertRedirects(response, url("cv"), fetch_redirect_response=False)
        run = store.load(mock.Mock(session=self.client.session))
        assert run is not None
        self.assertEqual(run.answers["job_titles"], DEFAULT_ANSWERS["titles"]["job_titles"])
        self.assertEqual(User.objects.filter(is_active=True).count(), 1)

    def test_city_suggestions_carry_the_bare_name_and_the_province(self):
        page = walk(self.client, until="villes")
        self.assertContains(page, '["Nivelles", "Brabant wallon"]')
        self.assertNotContains(page, "Nivelles — Brabant wallon")

    def test_chips_errors_keep_the_submitted_chips(self):
        walk(self.client, until="postes")
        response = self.client.post(url("postes"), {"titles": [f"Poste {i}" for i in range(11)]})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "10 intitulés, c&#x27;est déjà beaucoup.")
        self.assertContains(response, 'value="Poste 10"')

    def test_upload_requests_get_the_redirect_in_a_header(self):
        walk(self.client, until="salaire")
        response = self.client.post(
            url("horizon"), {"choice": "asap"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["X-Onboarding-Redirect"], url("salaire"))
        page = self.client.get(url("salaire"))
        self.assertContains(page, "Reprends ici")

    def test_completing_twice_is_harmless(self):
        walk(self.client)
        user = User.objects.get()
        onboarded_at = profile_for(user).onboarded_at
        response = self.client.post(url("plan"), {"action": "continue"})
        self.assertRedirects(response, reverse("tracker:dashboard"), fetch_redirect_response=False)
        self.assertEqual(SearchProfile.objects.count(), 1)
        self.assertEqual(profile_for(user).onboarded_at, onboarded_at)

    def test_every_step_renders(self):
        """Every screen, with a fully answered run, defines the icons it uses and says « tu »."""
        user = make_user("Sans nom", onboarded=False)
        Profile.objects.filter(user=user).update(display_name="")
        self.client.force_login(user)
        visited = frozenset(step.id for step in machine.steps if step.id != machine.terminal)
        session = self.client.session
        session[store.SESSION_KEY] = Run(state="plan", visited=visited, answers=all_answers()).to_json()
        session.save()
        for step in machine.steps:
            if step.id == machine.terminal or step.id == "welcome_back":
                continue
            with self.subTest(step=step.id):
                # The copilot interstitial is on the path only when the copilot listens.
                plugin = mock.patch("accounts.onboarding.services.cv_analyzer", new=lambda: object())
                with plugin if step.id == "intro_copilot" else mock.patch.object(self, "id"):
                    page = self.client.get(url(step.slug))
                self.assertEqual(page.status_code, 200, page.content[:300])
                html = page.content.decode()
                defined = set(re.findall(r'<symbol id="([^"]+)"', html))
                for used in re.findall(r'<use href="#([^"]+)"', html):
                    self.assertIn(used, defined)
                body = re.sub(r"<svg.*?</svg>", "", html, flags=re.S)
                self.assertNotRegex(body, r"\b[Vv]ous\b|\b[Vv]otre\b|\b[Vv]os\b")

    def test_no_number_is_fabricated(self):
        seen: list[str] = []
        for slug in ("situation", "rythme", "postes", "secteurs", "bilan", "identite", "cv", "plan"):
            page = walk(self.client, until=slug)
            seen.append(page.content.decode())
        for html in seen:
            body = re.sub(r"<svg.*?</svg>|<script.*?</script>", "", html, flags=re.S)
            for token in ("500 000", "500.000", "4,8", "4.8", "matches", "2 788", "2.788"):
                self.assertNotIn(token, body)


@override_settings(AUTH_MODE="accounts", SIGNUP_OPEN=True, PASSWORDLESS_AUTH=False, IS_SAAS_PRODUCTION=False)
@NO_PLUGIN
class AccountsFlowTests(TestCase):
    PASSWORD = "un-mot-de-passe-solide-42"
    SIGNUP = {
        "display_name": "Lionel",
        "email": "Lionel@Example.org",
        "password1": PASSWORD,
        "password2": PASSWORD,
    }

    def test_anonymous_can_answer_and_signs_up_at_the_gate(self):
        page = walk(self.client, until="identite")
        for field in ("display_name", "email", "password1", "password2"):
            self.assertContains(page, f'name="{field}"')
        self.assertContains(page, f'href="{reverse("accounts:login")}?next={ENTRY}"')
        self.assertContains(page, "17/19")
        response = self.client.post(url("identite"), self.SIGNUP)
        self.assertRedirects(response, url("cv"), fetch_redirect_response=False)
        user = User.objects.get()
        self.assertEqual((user.username, user.email), ("lionel@example.org", "lionel@example.org"))
        self.assertTrue(user.check_password(self.PASSWORD))
        profile = profile_for(user)
        self.assertEqual(profile.display_name, "Lionel")
        self.assertFalse(profile.is_onboarded)
        cv = self.client.get(url("cv"))
        self.assertEqual(cv.wsgi_request.user, user)
        self.assertContains(cv, "17/18")
        walk(self.client)
        self.assertTrue(profile_for(user).is_onboarded)

    def test_closed_signup_sends_anonymous_to_login_before_the_questionnaire(self):
        with self.settings(SIGNUP_OPEN=False):
            expected = f"{reverse('accounts:login')}?next={ENTRY}"
            self.assertRedirects(self.client.get(ENTRY), expected, fetch_redirect_response=False)
            self.assertRedirects(self.client.get(url("situation")), expected, fetch_redirect_response=False)

    def test_signup_page_sends_the_new_account_into_the_flow(self):
        response = self.client.post(reverse("accounts:signup"), self.SIGNUP)
        self.assertRedirects(response, ENTRY, fetch_redirect_response=False)
        user = User.objects.get()
        self.assertFalse(profile_for(user).is_onboarded)
        pages = []
        response = self.client.get(ENTRY)
        self.assertRedirects(response, url("situation"), fetch_redirect_response=False)
        for slug in ("bilan", "cv"):
            page = walk(self.client, until=slug)
            pages.append(page.content.decode())
        self.assertNotIn("Faisons connaissance", pages[1])
        self.assertIn("Ton CV", pages[1])
        walk(self.client)
        self.assertTrue(profile_for(user).is_onboarded)
        self.assertEqual(profile_for(user).display_name, "Lionel")

    def test_account_created_elsewhere_is_asked_its_name_at_the_gate(self):
        superuser = User.objects.create_superuser("root", "root@example.org", self.PASSWORD)
        self.client.force_login(superuser)
        self.assertRedirects(self.client.get(reverse("tracker:dashboard")), ENTRY, fetch_redirect_response=False)
        gate = walk(self.client, until="identite", name="Root")
        self.assertContains(gate, "Faisons connaissance")
        walk(self.client, name="Root")
        self.assertTrue(profile_for(superuser).is_onboarded)
        self.assertEqual(profile_for(superuser).display_name, "Root")
        self.assertEqual(self.client.get(reverse("tracker:dashboard")).status_code, 200)

    def test_gate_refuses_a_taken_email(self):
        make_user("Lionel", username="lionel@example.org", email="lionel@example.org")
        walk(self.client, until="identite")
        response = self.client.post(url("identite"), {**self.SIGNUP, "display_name": "Bis"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Un compte existe déjà avec cet e-mail.")
        self.assertEqual(User.objects.count(), 1)

    def test_htmx_fragment_from_a_non_onboarded_account_still_gets_hx_redirect(self):
        user = make_user("Sans profil", onboarded=False)
        self.client.force_login(user)
        response = self.client.get(reverse("tracker:stats_bar"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["HX-Redirect"], ENTRY)

    def test_login_link_from_the_gate_keeps_the_answers_for_an_unfinished_account(self):
        user = make_user("Lionel", username="lionel@example.org", email="lionel@example.org",
                         password=self.PASSWORD, onboarded=False)
        walk(self.client, until="identite")
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "lionel@example.org", "password": self.PASSWORD, "next": ENTRY},
        )
        self.assertRedirects(response, ENTRY, fetch_redirect_response=False)
        self.assertRedirects(self.client.get(ENTRY), url("cv"), fetch_redirect_response=False)
        run = store.load(mock.Mock(session=self.client.session))
        assert run is not None
        self.assertEqual(run.answers["job_titles"], DEFAULT_ANSWERS["titles"]["job_titles"])
        self.assertEqual(self.client.get(url("cv")).wsgi_request.user, user)


@override_settings(
    AUTH_MODE="accounts", SIGNUP_OPEN=True, PASSWORDLESS_AUTH=True,
    IS_SAAS_PRODUCTION=True, LAUNCH_INTEREST_ENABLED=True,
)
@NO_PLUGIN
class FinalAccountFlowTests(TestCase):
    SIGNUP = {"display_name": "Lionel", "email": "lionel@example.org"}

    def test_anonymous_finishes_questionnaire_before_final_account_preview(self):
        preview = walk(self.client, until="identite")
        self.assertTemplateUsed(preview, "accounts/onboarding/signup_preview.html")
        self.assertEqual(preview.context["n"], preview.context["total"])
        self.assertContains(preview, "Ingénieur DevOps")
        self.assertContains(preview, "Nivelles")
        for field in ("display_name", "email"):
            self.assertContains(preview, f'name="{field}"')
        self.assertNotContains(preview, 'type="password"')
        self.assertFalse(User.objects.exists())
        self.assertFalse(EmailSignInLink.objects.exists())
        self.assertFalse(SearchProfile.objects.exists())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_direct_identity_submission_cannot_skip_the_questionnaire(self):
        response = self.client.post(url("identite"), self.SIGNUP)
        self.assertRedirects(response, url("situation"), fetch_redirect_response=False)
        self.assertFalse(User.objects.exists())
        self.assertFalse(EmailSignInLink.objects.exists())

    def test_preview_get_keeps_answers_and_missing_email_cannot_complete(self):
        walk(self.client, until="identite")
        original = self.client.session[store.SESSION_KEY]
        self.assertEqual(self.client.get(url("identite")).status_code, 200)
        self.assertEqual(self.client.session[store.SESSION_KEY], original)
        response = self.client.post(url("identite"), {"display_name": "Lionel"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.assertEqual(response.context["form"]["display_name"].value(), "Lionel")
        self.assertEqual(self.client.session[store.SESSION_KEY], original)
        self.assertFalse(User.objects.exists())
        self.assertFalse(EmailSignInLink.objects.exists())

    def test_anonymous_cv_and_plan_requests_cannot_bypass_the_final_gate(self):
        for reached_gate in (False, True):
            if reached_gate:
                walk(self.client, until="identite")
            destination = url("identite" if reached_gate else "situation")
            for slug in ("cv", "plan"):
                for method in (self.client.get, self.client.post):
                    with self.subTest(gate=reached_gate, slug=slug, method=method.__name__):
                        response = method(url(slug), {"action": "skip", **self.SIGNUP})
                        self.assertRedirects(response, destination, fetch_redirect_response=False)
            self.assertFalse(User.objects.exists())
            self.assertFalse(Document.objects.exists())
            self.assertFalse(EmailSignInLink.objects.exists())

    def test_editing_the_review_updates_the_final_signup_snapshot(self):
        walk(self.client, until="identite")
        self.client.post(url("bilan"), {"action": "edit", "target": "postes"})
        self.client.post(url("postes"), {"titles": ["Comptable"]})
        self.client.post(url("bilan"), {"action": "continue"})
        preview = self.client.get(url("identite"))
        self.assertContains(preview, "Comptable")
        self.client.post(url("identite"), self.SIGNUP)
        snapshot = EmailSignInLink.objects.get().onboarding_data
        self.assertEqual(snapshot["state"], "done")
        self.assertEqual(snapshot["answers"]["job_titles"], ["Comptable"])
        self.assertEqual(snapshot["answers"]["cities"], ["Nivelles", "Wavre"])
        self.assertFalse(User.objects.exists())

    def test_final_signup_skips_pricing_without_opt_in_or_premium_grants(self):
        from accounts.magic_links import sign_link

        walk(self.client, until="identite")
        self.client.post(url("identite"), {
            **self.SIGNUP, "launch_notify": True, "launch_email": "lionel@example.org",
            "launch_plan": "premium", "plan": "premium", "consent": "on", "subscription_level": "premium",
        })
        link = EmailSignInLink.objects.get()
        self.assertNotIn("cv", link.onboarding_data["visited"])
        self.assertNotIn("plan", link.onboarding_data["visited"])
        response = self.client.post(reverse("accounts:email_link_confirm", args=[sign_link(link)]))
        self.assertRedirects(response, reverse("tracker:dashboard"))
        user = User.objects.get()
        profile = profile_for(user)
        self.assertTrue(profile.is_onboarded)
        self.assertIsNone(profile.launch_consent_at)
        self.assertFalse(has_premium(user))
        self.assertFalse(LaunchEmailJob.objects.exists())
        self.assertFalse(Document.objects.exists())


class ServiceTests(TestCase):
    def test_finish_onboarding_is_atomic_and_idempotent(self):
        user = make_user("Lionel", onboarded=False)
        answers = all_answers()
        finish_onboarding(user, answers)
        first = profile_for(user).onboarded_at
        finish_onboarding(user, answers)
        self.assertEqual(SearchProfile.objects.count(), 1)
        self.assertEqual(profile_for(user).onboarded_at, first)

        other = make_user("Marie", onboarded=False)
        with mock.patch.object(SearchProfile, "save", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                finish_onboarding(other, answers)
        self.assertFalse(profile_for(other).is_onboarded)
        self.assertFalse(SearchProfile.objects.filter(user=other).exists())

    def test_finish_onboarding_keeps_an_existing_headline_and_location(self):
        user = make_user("Lionel", onboarded=False, headline="SRE", location="Mons")
        finish_onboarding(user, all_answers())
        profile = profile_for(user)
        self.assertEqual((profile.headline, profile.location), ("SRE", "Mons"))
        self.assertTrue(profile.is_onboarded)

    def test_finish_onboarding_leaves_a_changed_follow_up_delay_alone(self):
        user = make_user("Lionel", onboarded=False)
        preferences = preferences_for(user)
        preferences.follow_up_days = 12
        preferences.save()
        finish_onboarding(user, {**all_answers(), "employment_status": "unemployed_urgent"})
        self.assertEqual(preferences_for(user).follow_up_days, 12)

    def test_name_profile_sets_the_name_only(self):
        user = make_user("Lionel", onboarded=False)
        name_profile(user, "  Marie ")
        profile = profile_for(user)
        self.assertEqual(profile.display_name, "Marie")
        self.assertFalse(profile.is_onboarded)

    def test_default_posts_cover_every_answering_step(self):
        answering = {step.id for step in machine.steps if step.answer_keys}
        self.assertEqual(answering, set(DEFAULT_POSTS))


# ---------------------------------------------------------------------------
# The CV step
# ---------------------------------------------------------------------------

MEMORY_STORAGES = {
    "default": {
        "BACKEND": "tracker.adapters.file_storage.MemoryStorageAdapter",
        "OPTIONS": {"link_ttl": 60},
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def blank_pdf() -> bytes:
    """A one-page PDF without a text layer (a scan, as far as extraction goes)."""
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def with_analyzer(analyzer):
    return mock.patch("accounts.onboarding.services.cv_analyzer", new=lambda: analyzer)


@override_settings(AUTH_MODE="local", STORAGES=MEMORY_STORAGES)
class CVStepTests(TestCase):
    def setUp(self):
        self.user = make_user("Lionel", onboarded=False)
        self.client.force_login(self.user)
        self.put_run_at_cv()

    def put_run_at_cv(self, **extra):
        answers = {k: v for k, v in all_answers().items() if not k.startswith("cv_")}
        answers.update(extra)
        visited = frozenset(s.id for s in machine.steps if s.id not in ("cv", "plan", "done", "welcome_back", "identity", "intro_copilot"))
        session = self.client.session
        session[store.SESSION_KEY] = Run(state="cv", visited=visited, answers=answers).to_json()
        session.save()

    def upload(self, name: str, content: bytes, *, analyzer=None, language: str = "fr", **fields):
        from django.core.files.uploadedfile import SimpleUploadedFile

        with with_analyzer(analyzer):
            return self.client.post(
                url("cv"),
                {"action": "upload", "file": SimpleUploadedFile(name, content), "language": language, **fields},
            )

    def current_run(self) -> Run:
        run = store.load(mock.Mock(session=self.client.session))
        assert run is not None
        return run

    def test_upload_files_a_primary_library_cv_and_shows_the_honest_card(self):
        from accounts.onboarding.testing import docx_bytes
        from tracker.models import Document, DocumentKind

        response = self.upload("CV Lionel Hubaut.docx", docx_bytes(), language="en")
        self.assertRedirects(response, url("cv"), fetch_redirect_response=False)
        document = Document.objects.get()
        self.assertEqual((document.owner, document.kind, document.application), (self.user, DocumentKind.CV, None))
        self.assertTrue(document.is_primary)
        self.assertEqual(document.language, "en")
        self.assertEqual(self.current_run().answers["cv_document_id"], document.pk)
        self.assertEqual(self.current_run().state, "cv")
        with with_analyzer(None):
            card = self.client.get(url("cv"))
        self.assertContains(card, "Ton CV est enregistré.")
        self.assertContains(card, "CV Lionel Hubaut.docx")
        self.assertContains(card, "DOCX")
        self.assertContains(card, "Copilote désactivé sur cette instance")
        self.assertNotContains(card, "Texte lisible")
        self.assertContains(card, "Remplacer ce fichier")
        with with_analyzer(None):
            response = self.client.post(url("cv"), {"action": "continue"})
        self.assertRedirects(response, url("plan"), fetch_redirect_response=False)

    def test_analyzer_receives_anonymised_text_and_the_card_says_so(self):
        from accounts.onboarding.testing import CV_BODY, FakeAnalyzer, docx_bytes

        FakeAnalyzer.reset()
        analyzer = FakeAnalyzer()
        self.upload("cv.docx", docx_bytes(), analyzer=analyzer)
        self.assertEqual(len(FakeAnalyzer.calls), 1)
        call = FakeAnalyzer.calls[0]
        self.assertEqual(set(call) - {"owner"}, {"document_id", "label", "language", "text"})
        self.assertNotIn("Lionel", call["text"].text)
        self.assertNotIn("lionel@example.org", call["text"].text)
        self.assertIn(CV_BODY.split("Nivelles")[0][:20].split(" — ")[-1], call["text"].text)
        answers = self.current_run().answers
        self.assertTrue(answers["cv_analyzed"])
        self.assertGreater(answers["cv_text_chars"], 200)
        with with_analyzer(analyzer):
            card = self.client.get(url("cv"))
        self.assertContains(card, "Transmis au copilote")
        self.assertContains(card, "Texte lisible")
        self.assertContains(card, "masqué")

    def test_scan_without_text_is_stored_and_flagged(self):
        from accounts.onboarding.testing import FakeAnalyzer
        from tracker.models import Document

        FakeAnalyzer.reset()
        analyzer = FakeAnalyzer()
        self.upload("scan.pdf", blank_pdf(), analyzer=analyzer)
        self.assertEqual(Document.objects.count(), 1)
        self.assertEqual(FakeAnalyzer.calls, [])
        with with_analyzer(analyzer):
            card = self.client.get(url("cv"))
        self.assertContains(card, "Aucun texte lisible")
        self.assertContains(card, "Trop court ou illisible")

    def test_a_copilot_that_fails_is_reported_as_such(self):
        from accounts.onboarding.testing import docx_bytes

        class Broken:
            def analyze_cv(self, owner, **kwargs):
                raise RuntimeError("boom")

        self.upload("cv.docx", docx_bytes(), analyzer=Broken())
        with with_analyzer(Broken()):
            card = self.client.get(url("cv"))
        self.assertContains(card, "Texte lisible")
        self.assertContains(card, "n'a pas pu l'analyser")
        self.assertNotContains(card, "Trop court")

    def test_a_forged_replacing_value_is_ignored(self):
        from accounts.onboarding.testing import docx_bytes
        from tracker.models import Document

        response = self.upload("cv.docx", docx_bytes(), replacing="²")
        self.assertRedirects(response, url("cv"), fetch_redirect_response=False)
        self.assertTrue(Document.objects.get().is_primary)

    def test_short_text_is_kept_but_not_analysed(self):
        from accounts.onboarding.testing import FakeAnalyzer, docx_bytes

        FakeAnalyzer.reset()
        analyzer = FakeAnalyzer()
        self.upload("cv.docx", docx_bytes("Bonjour."), analyzer=analyzer)
        self.assertEqual(FakeAnalyzer.calls, [])
        with with_analyzer(analyzer):
            card = self.client.get(url("cv"))
        self.assertContains(card, "Trop court ou illisible")
        self.assertFalse(self.current_run().answers["cv_analyzed"])

    def test_replace_demotes_the_previous_upload_and_deletes_nothing(self):
        from accounts.onboarding.testing import docx_bytes
        from tracker.models import Document

        self.upload("premier.docx", docx_bytes())
        first = Document.objects.get()
        with with_analyzer(None):
            page = self.client.get(url("cv") + "?remplacer=1")
        self.assertContains(page, f'name="replacing" value="{first.pk}"')
        self.assertContains(page, "Garder le CV actuel")
        self.upload("second.docx", docx_bytes(), replacing=str(first.pk))
        first.refresh_from_db()
        second = Document.objects.exclude(pk=first.pk).get()
        self.assertFalse(first.is_primary)
        self.assertTrue(second.is_primary)
        self.assertEqual(self.current_run().answers["cv_document_id"], second.pk)

    def test_existing_primary_library_cv_is_not_demoted_by_a_first_upload(self):
        from accounts.onboarding.testing import docx_bytes
        from tracker.models import Document, DocumentKind

        imported = Document.objects.create(
            owner=self.user, kind=DocumentKind.CV, label="CV de base", is_primary=True,
            file="documents/x/bibliotheque/base.docx",
        )
        self.upload("nouveau.docx", docx_bytes())
        imported.refresh_from_db()
        self.assertTrue(imported.is_primary)
        self.assertFalse(Document.objects.exclude(pk=imported.pk).get().is_primary)

    def test_rejects_unsupported_suffix_and_oversize(self):
        from tracker.models import Document

        response = self.upload("cv.exe", b"MZ")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Format non pris en charge")
        with self.settings(FILE_UPLOAD_MAX_MEMORY_SIZE=2 * 1024 * 1024):
            response = self.upload("cv.pdf", b"%PDF-" + b"0" * (3 * 1024 * 1024))
        self.assertContains(response, "Fichier trop lourd : 2 Mo max.")
        self.assertFalse(Document.objects.exists())

    def test_upload_hint_is_by_the_picker_and_uses_the_configured_limit(self):
        with with_analyzer(None), self.settings(FILE_UPLOAD_MAX_MEMORY_SIZE=2 * 1024 * 1024):
            response = self.client.get(url("cv"))
        self.assertContains(response, "PDF (.pdf) ou DOCX (.docx) · 2 Mo max.")
        self.assertContains(response, 'aria-describedby="id_file_helptext"')
        self.assertContains(response, 'accept=".pdf,.docx"')
        self.assertNotContains(response, "20 Mo max.")
        zone = response.content.decode().split("data-dropzone>", 1)[1].split("</label>", 1)[0]
        self.assertIn('id="id_file_helptext"', zone)

    def test_skip_writes_null_and_the_language_feeds_preferences_only_when_given(self):
        from accounts.onboarding.testing import docx_bytes

        with with_analyzer(None):
            response = self.client.post(url("cv"), {"action": "skip"})
        self.assertRedirects(response, url("plan"), fetch_redirect_response=False)
        self.assertIsNone(self.current_run().answers["cv_document_id"])
        with with_analyzer(None):
            self.client.post(url("plan"), {"action": "continue"})
        self.assertEqual(preferences_for(self.user).default_cv_language, "fr")

        self.client.logout()
        other = make_user("Marie", onboarded=False)
        self.client.force_login(other)
        self.user = other
        self.put_run_at_cv()
        self.upload("cv.docx", docx_bytes(), language="nl")
        with with_analyzer(None):
            self.client.post(url("cv"), {"action": "continue"})
            self.client.post(url("plan"), {"action": "continue"})
        self.assertEqual(preferences_for(other).default_cv_language, "nl")
        self.assertTrue(profile_for(other).is_onboarded)

    def test_continue_without_upload_is_refused(self):
        with with_analyzer(None):
            response = self.client.post(url("cv"), {"action": "continue"}, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], url("cv"))
        self.assertContains(response, "Envoie un fichier, ou passe cette étape.")
        self.assertEqual(self.current_run().state, "cv")


@skipUnless(apps.is_installed("jobhunt_ai"), "Copilot is disabled")
@override_settings(AUTH_MODE="accounts", STORAGES=MEMORY_STORAGES, IS_SAAS_PRODUCTION=False)
class CVAnalysisProgressTests(TestCase):
    def setUp(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from accounts.onboarding.testing import docx_bytes
        from tracker.events import CVEventPublisher

        self.user = make_user("Candidate", onboarded=False)
        self.client.force_login(self.user)
        session = self.client.session
        session[store.SESSION_KEY] = Run(state="cv", answers={}).to_json()
        session.save()
        with with_analyzer(CVEventPublisher()), mock.patch("jobhunt_ai.conf.EAGER_RUNS", False):
            response = self.client.post(url("cv"), {
                "action": "upload", "language": "fr", "file": SimpleUploadedFile("cv.docx", docx_bytes()),
            })
        self.assertRedirects(response, url("cv"), fetch_redirect_response=False)
        from jobhunt_ai.models import AgentRun

        self.analysis = AgentRun.objects.get(owner=self.user)
        self.status_url = reverse("accounts:onboarding_cv_status")

    def test_free_onboarding_queues_then_extracts_details_and_stops_polling(self):
        from jobhunt_ai.models import CandidateProfile, RunStatus
        from jobhunt_ai.services import runner
        from jobhunt_ai.tests.fakes import fake_llm

        self.assertIsNone(profile_for(self.user).premium_until)
        self.assertEqual(self.analysis.status, RunStatus.PENDING)
        page = self.client.get(url("cv"))
        self.assertContains(page, 'data-status="pending"')
        queued = self.client.get(self.status_url)
        self.assertContains(queued, 'data-status="pending"')
        self.assertIn("no-store", queued.headers["Cache-Control"])

        with fake_llm() as calls:
            runner.execute(self.analysis.pk, self.user.pk)
        self.assertEqual(len(calls), 1)
        profile = CandidateProfile.objects.get(owner=self.user)
        self.assertIsNotNone(profile.source_document)
        assert profile.source_document is not None
        self.assertEqual(profile.source_document.pk, self.analysis.params["document_id"])
        self.assertEqual(profile.skills[0]["name"], "Ansible")
        finished = self.client.get(self.status_url)
        self.assertContains(finished, 'data-status="succeeded"')
        self.assertContains(finished, "Ansible")
        self.assertContains(finished, profile.summary, html=True)
        self.assertContains(finished, "Expériences professionnelles")
        self.assertContains(finished, "Ingénieur DevOps")
        self.assertContains(finished, "Acme")
        self.assertContains(finished, "2019")
        self.assertContains(finished, "aujourd’hui")
        self.assertNotContains(finished, 'data-status="pending"')

    def test_experiences_preserve_dates_and_do_not_infer_current_roles(self):
        from jobhunt_ai.models import CandidateProfile

        profile = CandidateProfile.objects.create(
            owner=self.user, source_document_id=self.analysis.params["document_id"],
            summary="Synthèse de démonstration.",
            experiences=[
                {"title": "Architecte logiciel", "company": "Acme", "start": "2021-03", "end": "2025-08", "current": False},
                {"title": "Consultant", "company": "", "start": "2018", "end": "", "current": False},
                {"title": "Développeur", "company": "Studio", "start": "", "end": "2017", "current": False},
                {"title": "Stage", "company": "", "start": "", "end": "", "current": False},
                {"title": "  ", "company": "Atelier", "start": "2016", "end": "2017", "current": False},
            ],
        )
        self.analysis.status = "running"
        self.analysis.save(update_fields=["status"])
        self.analysis.mark_succeeded({"profile_id": profile.pk})
        for address in (url("cv"), self.status_url):
            with self.subTest(address=address):
                page = self.client.get(address)
                for text in ("Architecte logiciel", "Acme", "2021-03", "2025-08", "2018", "2017",
                             "Fin non précisée", "Début non précisé", "Dates non précisées"):
                    self.assertContains(page, text)
                self.assertContains(page, "Des informations sont à vérifier")
                self.assertContains(page, '<li><strong>Consultant</strong><span>Informations absentes : entreprise, date de fin.</span></li>', html=True)
                self.assertContains(page, '<li><strong>Développeur · Studio</strong><span>Informations absentes : date de début.</span></li>', html=True)
                self.assertContains(page, '<li><strong>Stage</strong><span>Informations absentes : entreprise, date de début, date de fin.</span></li>', html=True)
                self.assertContains(page, '<li><strong>Poste non précisé · Atelier</strong><span>Informations absentes : intitulé du poste.</span></li>', html=True)
                self.assertNotContains(page, "<strong>Architecte logiciel")
                content = page.content.decode()
                self.assertLess(content.index('aria-labelledby="cv-review-title"'), content.index('class="cv-analysis__summary"'))
                self.assertContains(page, 'role="status" aria-labelledby="cv-review-title"')
                self.assertContains(page, "Compare les postes, les entreprises, les dates, les langues et leurs niveaux avec ton CV d’origine.")
                self.assertContains(page, "des expériences oubliées ou des dates incorrectes")
                self.assertNotContains(page, "aujourd’hui")

    def test_completed_card_omits_the_experience_list_when_none_were_extracted(self):
        from jobhunt_ai.models import CandidateProfile

        profile = CandidateProfile.objects.create(
            owner=self.user, source_document_id=self.analysis.params["document_id"], experiences=[],
        )
        self.analysis.status = "running"
        self.analysis.save(update_fields=["status"])
        self.analysis.mark_succeeded({"profile_id": profile.pk})
        for address in (url("cv"), self.status_url):
            with self.subTest(address=address):
                page = self.client.get(address)
                self.assertNotContains(page, "Expériences professionnelles")
                self.assertContains(page, "Des informations sont à vérifier")
                self.assertContains(page, "Aucune expérience professionnelle n’a été extraite.")
                self.assertContains(page, "Si ton CV contient des expériences professionnelles, elles peuvent avoir été omises.")
                self.assertContains(page, "Compare les postes, les entreprises, les dates, les langues et leurs niveaux avec ton CV d’origine.")
                self.assertNotContains(page, 'class="cv-analysis__gaps"')

    def test_complete_and_current_experiences_still_prompt_review_for_omissions(self):
        from jobhunt_ai.models import CandidateProfile

        profile = CandidateProfile.objects.create(
            owner=self.user, source_document_id=self.analysis.params["document_id"],
        )
        self.analysis.status = "running"
        self.analysis.save(update_fields=["status"])
        self.analysis.mark_succeeded({"profile_id": profile.pk})
        for current, end in ((True, ""), (False, "2024-06")):
            profile.experiences = [{
                "title": "QA Engineer", "company": "Exemple", "start": "2020-01", "end": end, "current": current,
            }]
            profile.save(update_fields=["experiences"])
            for address in (url("cv"), self.status_url):
                with self.subTest(current=current, address=address):
                    page = self.client.get(address)
                    self.assertContains(page, "Ton CV a été analysé.")
                    self.assertNotContains(page, "Ton parcours est prêt.")
                    self.assertContains(page, "Vérifie les informations extraites")
                    self.assertContains(page, "Compare les postes, les entreprises, les dates, les langues et leurs niveaux avec ton CV d’origine.")
                    self.assertContains(page, "Une mise en page complexe peut entraîner des expériences oubliées ou des dates incorrectes")
                    self.assertContains(page, "même si les lignes affichées semblent complètes.")
                    self.assertNotContains(page, "Des informations sont à vérifier")
                    self.assertNotContains(page, "Informations absentes")
                    self.assertNotContains(page, 'class="cv-analysis__gaps"')
                    self.assertNotContains(page, "Fin non précisée")
                    self.assertContains(page, "aujourd’hui" if current else end)

    def complete_with_languages(self, languages):
        from jobhunt_ai.models import CandidateProfile

        profile = CandidateProfile.objects.create(
            owner=self.user, source_document_id=self.analysis.params["document_id"], languages=languages,
        )
        self.analysis.status = "running"
        self.analysis.save(update_fields=["status"])
        self.analysis.mark_succeeded({"profile_id": profile.pk})
        return profile

    def test_extracted_languages_show_the_saved_proficiency_on_page_and_polled_card(self):
        self.complete_with_languages([
            {"name": "Français", "level": "langue maternelle"},
            {"name": "Anglais", "level": "C1"},
            {"name": "Néerlandais", "level": "notions"},
        ])
        for address in (url("cv"), self.status_url):
            with self.subTest(address=address):
                page = self.client.get(address)
                self.assertContains(page, '<h3 class="cv-analysis__subtitle" id="cv-languages-title">Langues</h3>', html=True)
                self.assertContains(page, 'aria-labelledby="cv-languages-title"')
                for name, level in (("Français", "langue maternelle"), ("Anglais", "C1"), ("Néerlandais", "notions")):
                    self.assertContains(page, f'<div><dt>{name}</dt><dd>{level}</dd></div>', html=True)
                self.assertContains(page, "3 langues")
                self.assertContains(page, "les langues et leurs niveaux avec ton CV d’origine")
                self.assertNotContains(page, "Niveau non précisé")
                self.assertNotContains(page, "Aucune langue identifiée dans ce CV.")

    def test_language_section_distinguishes_empty_extraction_and_unspecified_levels(self):
        profile = self.complete_with_languages([])
        for address in (url("cv"), self.status_url):
            with self.subTest(empty=True, address=address):
                page = self.client.get(address)
                self.assertContains(page, "Aucune langue identifiée dans ce CV.")
                self.assertContains(page, "0 langues")
                self.assertNotContains(page, 'class="cv-analysis__languages"')
        profile.languages = [
            {"name": "Allemand"}, {"name": "Italien", "level": ""}, {"name": "Portugais", "level": " \t "},
        ]
        profile.save(update_fields=["languages"])
        for address in (url("cv"), self.status_url):
            with self.subTest(empty=False, address=address):
                page = self.client.get(address)
                for name in ("Allemand", "Italien", "Portugais"):
                    self.assertContains(page, f'<div><dt>{name}</dt><dd>Niveau non précisé</dd></div>', html=True)
                self.assertContains(page, "Niveau non précisé", count=3)
                self.assertNotContains(page, "Aucune langue identifiée dans ce CV.")

    def test_extracted_language_names_and_levels_are_escaped(self):
        from django.utils.html import escape

        name = '<script>alert("langue")</script>'
        level = '<img src=x onerror="alert(1)">'
        self.complete_with_languages([{"name": name, "level": level}])
        for address in (url("cv"), self.status_url):
            with self.subTest(address=address):
                page = self.client.get(address)
                self.assertContains(page, escape(name))
                self.assertContains(page, escape(level))
                self.assertNotContains(page, name)
                self.assertNotContains(page, level)
                self.assertContains(page, "1 langue")

    def test_processing_phase_and_failure_are_honest_without_exposing_provider_errors(self):
        from jobhunt_ai.models import RunStatus

        self.analysis.status = RunStatus.RUNNING
        self.analysis.phase = "Extraction des compétences et des expériences"
        self.analysis.save(update_fields=["status", "phase"])
        processing = self.client.get(self.status_url)
        self.assertContains(processing, 'data-status="running"')
        self.assertContains(processing, self.analysis.phase)
        self.analysis.mark_failed("private-provider-error-with-document-text")
        failed = self.client.get(self.status_url)
        self.assertContains(failed, 'data-status="failed"')
        self.assertNotContains(failed, "private-provider-error")

    def test_expired_work_is_reconciled_during_polling(self):
        from datetime import timedelta
        from django.utils import timezone
        from jobhunt_ai.models import RunStatus

        self.analysis.deadline_at = timezone.now() - timedelta(seconds=1)
        self.analysis.save(update_fields=["deadline_at"])
        response = self.client.get(self.status_url)
        self.assertContains(response, 'data-status="failed"')
        self.analysis.refresh_from_db()
        self.assertEqual(self.analysis.status, RunStatus.FAILED)

    def test_status_is_scoped_to_the_session_document_and_signed_in_owner(self):
        from jobhunt_ai.models import AgentRun, RunKind
        from tracker.models import Document, DocumentKind

        other = make_user("Other", onboarded=False)
        document = Document.objects.create(owner=other, kind=DocumentKind.CV, label="private CV")
        AgentRun.objects.create(owner=other, kind=RunKind.PARSE_CV, params={"document_id": document.pk})
        self.assertEqual(self.client.get(self.status_url, {"document": document.pk}).status_code, 409)
        session = self.client.session
        session[store.SESSION_KEY] = Run(state="cv", answers={"cv_document_id": document.pk}).to_json()
        session.save()
        self.assertEqual(self.client.get(self.status_url).status_code, 404)
        self.client.logout()
        response = self.client.get(self.status_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.headers["Location"])

    def test_polling_does_not_change_onboarding_answers_and_requires_get(self):
        previous = dict(self.client.session[store.SESSION_KEY])
        self.client.get(self.status_url)
        self.assertEqual(self.client.session[store.SESSION_KEY], previous)
        self.assertEqual(self.client.post(self.status_url).status_code, 405)


# ---------------------------------------------------------------------------
# Row-level security
# ---------------------------------------------------------------------------


class CoverageTests(TestCase):
    def test_the_search_profile_is_registered_and_covered(self):
        from rls import checks, registry

        self.assertIsNotNone(registry.rule_for(SearchProfile))
        uncovered = [w.msg for w in checks.check_coverage(None) if w.msg.startswith("accounts.")]
        self.assertEqual(uncovered, [])


@skipUnless(connection.vendor == "postgresql", "les politiques ne vivent que sur PostgreSQL")
class RlsTests(TestCase):
    """The rows are made by the superuser connection; the policies are read as the application role."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user("Alice", username="alice")
        cls.bob = make_user("Bob", username="bob")
        SearchProfile.objects.create(user=cls.alice, job_titles=["Ingénieure"])

    def test_search_profile_is_invisible_to_another_account_and_to_nobody(self):
        with app_role(), as_user(self.alice):
            self.assertEqual(SearchProfile.objects.get().user, self.alice)
        with app_role(), as_user(self.bob):
            self.assertEqual(SearchProfile.objects.count(), 0)
        with app_role(), as_user(None):
            self.assertEqual(SearchProfile.objects.count(), 0)

    def test_the_application_role_writes_its_own_search_profile_only(self):
        carol = make_user("Carol", username="carol")
        with app_role(), as_user(self.bob):
            SearchProfile.objects.create(user=self.bob, cities=["Mons"])
            self.assertEqual(SearchProfile.objects.get().user, self.bob)
            # Another account's row is invisible: the update touches nothing.
            self.assertEqual(SearchProfile.objects.filter(user=self.alice).update(cities=["Liège"]), 0)
            # A row for another account is refused by the policy itself.
            with self.assertRaises(ProgrammingError), transaction.atomic():
                SearchProfile.objects.create(user=carol, cities=["Namur"])
        self.assertEqual(SearchProfile.objects.get(user=self.alice).cities, [])
        self.assertFalse(SearchProfile.objects.filter(user=carol).exists())
