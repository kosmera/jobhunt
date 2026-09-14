"""Tests des pages et fragments HTMX du copilote."""

from __future__ import annotations

import shutil
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from accounts.models import Profile, SubscriptionLevel
from tracker.models import Application, Platform, Status

from jobhunt_ai.models import (
    LEAD_SUMMARY_PREFIX,
    AgentRun,
    CandidateProfile,
    LeadStatus,
    MatchReport,
    OfferLead,
    RunKind,
    RunStatus,
)
from jobhunt_ai.tests import fakes
from jobhunt_ai.tests.fakes import PremiumTestCase
from jobhunt_ai.tests.test_agents import CV_TEXT, make_application, make_profile

MEDIA = tempfile.mkdtemp(prefix="jobhunt-ai-view-tests-")


@override_settings(MEDIA_ROOT=MEDIA)
class PageRenderTests(PremiumTestCase):
    """Chaque page doit se rendre vide, puis avec des données."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def test_pages_render_empty(self):
        for route in ("jobhunt_ai:copilot", "jobhunt_ai:profile", "jobhunt_ai:leads"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)

    def test_pages_render_with_data(self):
        profile = make_profile(
            experiences=[{"title": "Ingénieur", "company": "Acme", "start": "2019",
                          "current": True, "achievements": ["Une réussite"]}],
            education=[{"degree": "Master", "school": "UCL", "year": "2008"}],
            languages=[{"name": "Français", "level": "C2"}],
            certifications=[{"name": "RHCE", "issuer": "Red Hat", "year": "2015"}],
            links=[{"label": "LinkedIn", "url": "https://example.org"}],
        )
        CandidateProfile.objects.create(owner=self.user, label="CV anglais")
        application = make_application()
        run = AgentRun.objects.create(
            owner=self.user, kind=RunKind.MATCH, status=RunStatus.SUCCEEDED,
            application=application, result={"score": 76},
        )
        MatchReport.objects.create(
            application=application, profile=profile, run=run, score=76,
            summary="Bon recouvrement.", missing_skills=["Terraform"],
        )
        OfferLead.objects.create(
            owner=self.user, title="DevOps", company_name="Widgets", score=81,
            score_reason="Stack alignée.", url="https://example.org/o/1",
        )
        for route in ("jobhunt_ai:copilot", "jobhunt_ai:profile", "jobhunt_ai:leads"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)

    def test_rail_shows_the_account_not_the_candidate_profile(self):
        """La vue passe ``profile`` (profil candidat) : le rail lit le compte
        sur ``request.profile`` et ne doit pas être écrasé."""
        make_profile(full_name="Nom du CV")
        response = self.client.get(reverse("jobhunt_ai:copilot"))
        self.assertContains(response, 'class="user-chip__name">Lionel<')

    def test_navigation_shows_copilot_entry(self):
        response = self.client.get(reverse("jobhunt_ai:copilot"))
        self.assertContains(response, "Copilote")
        self.assertContains(response, "i-sparkle")

    def test_leads_badge_counts_new_leads_of_the_account_only(self):
        from accounts.testing import make_user

        OfferLead.objects.create(owner=self.user, title="A", company_name="B")
        OfferLead.objects.create(owner=make_user("Marie"), title="C", company_name="D")
        response = self.client.get(reverse("tracker:dashboard"))
        self.assertEqual(response.context["nav_counters"]["ai_leads"], 1)

    def test_application_detail_shows_copilot_panel(self):
        application = make_application()
        response = self.client.get(application.get_absolute_url())
        self.assertContains(response, 'id="ai-panel"')
        self.assertContains(response, "Évaluer la compatibilité")

    def test_free_account_needs_premium_even_in_debug(self):
        Profile.objects.filter(user=self.user).update(subscription_level=SubscriptionLevel.FREE)
        with override_settings(DEBUG=True):
            response = self.client.get(reverse("jobhunt_ai:copilot"))
        self.assertEqual(response.status_code, 402)
        self.assertContains(response, "Premium", status_code=402)
        self.assertNotContains(response, "JOBHUNT_AI_", status_code=402)


@override_settings(MEDIA_ROOT=MEDIA)
class ParseCVViewTests(PremiumTestCase):
    def test_upload_launches_parse_and_creates_library_document(self):
        upload = SimpleUploadedFile("cv.txt", CV_TEXT.encode("utf-8"))
        with fakes.fake_llm(), fakes.eager_runs():
            response = self.client.post(
                reverse("jobhunt_ai:parse_cv"),
                {"file": upload, "make_primary": "on"},
            )
        self.assertEqual(response.status_code, 200)
        run = AgentRun.objects.get()
        self.assertEqual(run.status, RunStatus.SUCCEEDED, run.error)
        self.assertTrue(CandidateProfile.objects.filter(is_primary=True).exists())
        from tracker.models import Document

        document = Document.objects.get()
        self.assertIsNone(document.application)

    def test_invalid_upload_rerenders_form_with_422(self):
        response = self.client.post(reverse("jobhunt_ai:parse_cv"), {})
        self.assertEqual(response.status_code, 422)
        self.assertContains(
            response, "Choisis un fichier ou un document existant.", status_code=422
        )

    def test_unsupported_extension_rejected(self):
        upload = SimpleUploadedFile("cv.rtf", b"not supported")
        response = self.client.post(reverse("jobhunt_ai:parse_cv"), {"file": upload})
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "Format non pris en charge", status_code=422)

    def test_the_upload_goes_through_the_core_and_is_anonymised(self):
        """La vue ne crée plus le document elle-même : ``services.ingest_cv``
        range les octets par le port, relit le texte et l'anonymise."""
        from tracker.models import Document

        upload = SimpleUploadedFile("cv.txt", CV_TEXT.encode("utf-8"))
        with fakes.fake_llm(), fakes.eager_runs():
            self.client.post(
                reverse("jobhunt_ai:parse_cv"), {"file": upload, "label": "Mon CV"}
            )
        document = Document.objects.get()
        self.assertEqual(document.label, "Mon CV")
        # Rangé par le cœur : le compte est dans le nom, la bibliothèque
        # aussi (le suffixe vient d'un nom déjà pris dans le MEDIA partagé).
        self.assertTrue(
            (document.file.name or "").startswith(f"documents/{self.user.pk}/bibliotheque/cv"),
            document.file.name,
        )
        self.assertIsNotNone(document.size_bytes)
        run = AgentRun.objects.get()
        self.assertEqual(run.params["document_id"], document.pk)
        self.assertNotIn("lionel.test@example.be", run.params["text"])
        self.assertIn("[EMAIL]", run.params["text"])

    def test_an_already_stored_document_is_reread_through_the_port(self):
        """L'autre branche du formulaire : le fichier est déjà rangé, on n'en
        réécrit pas les octets, on en relit le texte par le port."""
        from tracker.models import Document, DocumentKind

        document = Document.objects.create(
            owner=self.user, kind=DocumentKind.CV, label="CV déjà rangé",
            file=SimpleUploadedFile("deja.txt", CV_TEXT.encode("utf-8")),
        )
        with fakes.fake_llm(), fakes.eager_runs():
            response = self.client.post(
                reverse("jobhunt_ai:parse_cv"), {"document": document.pk}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Document.objects.count(), 1)
        run = AgentRun.objects.get()
        self.assertEqual(run.status, RunStatus.SUCCEEDED, run.error)
        self.assertEqual(run.params["document_id"], document.pk)
        self.assertEqual(CandidateProfile.objects.get().source_document, document)

    def test_a_cv_without_enough_text_is_stored_and_explained(self):
        """Plus de repli qui enverrait le PDF au modèle : on le dit."""
        from tracker.models import Document

        upload = SimpleUploadedFile("scan.txt", b"trois mots seulement")
        with fakes.fake_llm(), fakes.eager_runs():
            response = self.client.post(reverse("jobhunt_ai:parse_cv"), {"file": upload})
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "pas assez de", status_code=422)
        self.assertEqual(Document.objects.count(), 1)
        self.assertEqual(AgentRun.objects.count(), 0)

    def test_a_cv_added_from_the_core_library_is_analysed_too(self):
        """Le descripteur branche l'analyseur sur *tout* CV téléversé, y
        compris depuis les pages du cœur."""
        from tracker.models import DocumentKind

        upload = SimpleUploadedFile("cv.txt", CV_TEXT.encode("utf-8"))
        with fakes.fake_llm(), fakes.eager_runs():
            response = self.client.post(
                reverse("tracker:add_library_document"),
                {"file": upload, "kind": DocumentKind.CV, "label": "CV bibliothèque"},
                HTTP_HX_REQUEST="true",
            )
        self.assertEqual(response.status_code, 204)
        run = AgentRun.objects.get()
        self.assertEqual(run.kind, RunKind.PARSE_CV)
        self.assertEqual(run.status, RunStatus.SUCCEEDED, run.error)
        profile = CandidateProfile.objects.get()
        self.assertEqual(profile.label, "CV bibliothèque")
        # Personne n'a demandé le profil principal : le premier l'est quand même.
        self.assertTrue(profile.is_primary)


class RunStatusViewTests(PremiumTestCase):
    def test_running_fragment_polls(self):
        run = AgentRun.objects.create(owner=self.user, kind=RunKind.MATCH, status=RunStatus.RUNNING)
        response = self.client.get(reverse("jobhunt_ai:run_status", args=[run.pk]))
        self.assertContains(response, "hx-trigger")

    def test_someone_else_s_run_is_a_404(self):
        from accounts.testing import make_user

        run = AgentRun.objects.create(owner=make_user("Marie"), kind=RunKind.MATCH, status=RunStatus.RUNNING)
        self.assertEqual(self.client.get(reverse("jobhunt_ai:run_status", args=[run.pk])).status_code, 404)

    def test_finished_poll_triggers_page_refresh(self):
        run = AgentRun.objects.create(
            owner=self.user, kind=RunKind.MATCH, status=RunStatus.SUCCEEDED, result={"score": 80}
        )
        response = self.client.get(
            reverse("jobhunt_ai:run_status", args=[run.pk]), {"poll": "1"}
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["HX-Refresh"], "true")

    def test_failed_fragment_shows_error(self):
        run = AgentRun.objects.create(
            owner=self.user, kind=RunKind.SCOUT, status=RunStatus.FAILED, error="Boum"
        )
        response = self.client.get(
            reverse("jobhunt_ai:run_status", args=[run.pk]), {"poll": "1"}
        )
        self.assertContains(response, "Boum")


class EvaluateViewTests(PremiumTestCase):
    def test_evaluate_without_profile_shows_toast_error(self):
        application = make_application()
        response = self.client.post(reverse("jobhunt_ai:evaluate", args=[application.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Analyse d'abord un CV", response.headers["HX-Trigger"])
        self.assertFalse(AgentRun.objects.exists())

    def test_evaluate_launches_match_run(self):
        make_profile()
        application = make_application()
        with fakes.fake_llm(), fakes.eager_runs():
            response = self.client.post(
                reverse("jobhunt_ai:evaluate", args=[application.pk])
            )
        self.assertEqual(response.status_code, 200)
        application.refresh_from_db()
        self.assertEqual(application.score, 76)

    def test_second_click_reuses_active_run(self):
        make_profile()
        application = make_application()
        existing = AgentRun.objects.create(
            owner=self.user, kind=RunKind.MATCH, status=RunStatus.RUNNING, application=application
        )
        response = self.client.post(reverse("jobhunt_ai:evaluate", args=[application.pk]))
        self.assertContains(response, f"run-{existing.pk}")
        self.assertEqual(AgentRun.objects.count(), 1)

    def test_another_account_s_scout_does_not_block_or_show_on_mine(self):
        from accounts.testing import make_user

        other = make_user("Marie")
        AgentRun.objects.create(owner=other, kind=RunKind.SCOUT, status=RunStatus.RUNNING)
        make_profile()
        self.assertIsNone(self.client.get(reverse("jobhunt_ai:leads")).context["active_run"])
        self.assertIsNone(self.client.get(reverse("jobhunt_ai:copilot")).context["active_run"])
        with fakes.fake_llm(), fakes.eager_runs(), mock.patch(
            "jobhunt_ai.scraping.sources.fetch_page_text", return_value="du texte"
        ):
            response = self.client.post(
                reverse("jobhunt_ai:scout_start"),
                {"keywords": "devops", "location": "Nivelles", "radius_km": 40},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AgentRun.objects.filter(owner=self.user, kind=RunKind.SCOUT).count(), 1)

    def test_evaluating_or_generating_for_someone_else_s_application_is_a_404(self):
        from accounts.testing import make_user
        from tracker.models import Document

        make_profile()
        theirs = make_application(owner=make_user("Marie"), company_name="Widgets")
        for route in ("jobhunt_ai:evaluate", "jobhunt_ai:generate_cv"):
            with self.subTest(route=route):
                response = self.client.post(reverse(route, args=[theirs.pk]), {"language": "fr"})
                self.assertEqual(response.status_code, 404)
        self.assertFalse(AgentRun.objects.exists())
        self.assertFalse(Document.objects.exists())

    def test_pages_never_show_another_account_s_rows(self):
        from accounts.testing import make_user

        other = make_user("Marie")
        make_profile(owner=other, label="Profil de Marie", full_name="Marie Curie")
        AgentRun.objects.create(owner=other, kind=RunKind.SCOUT, status=RunStatus.FAILED, error="Chez Marie")
        OfferLead.objects.create(owner=other, title="Poste de Marie", company_name="Widgets")
        make_profile()
        for route in ("jobhunt_ai:copilot", "jobhunt_ai:profile", "jobhunt_ai:leads"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "Marie")
                self.assertNotContains(response, "Widgets")
        self.assertEqual(self.client.get(reverse("jobhunt_ai:copilot")).context["profiles_count"], 1)
        self.assertEqual(list(self.client.get(reverse("jobhunt_ai:leads")).context["leads"]), [])


@override_settings(MEDIA_ROOT=MEDIA)
class GenerateCVViewTests(PremiumTestCase):
    def test_generate_launches_run_with_language(self):
        make_profile()
        application = make_application()
        with fakes.fake_llm(), fakes.eager_runs():
            response = self.client.post(
                reverse("jobhunt_ai:generate_cv", args=[application.pk]),
                {"language": "en"},
            )
        self.assertEqual(response.status_code, 200)
        run = AgentRun.objects.get()
        self.assertEqual(run.status, RunStatus.SUCCEEDED, run.error)
        self.assertEqual(run.result["language"], "en")


class ScoutViewTests(PremiumTestCase):
    def test_scout_requires_profile(self):
        response = self.client.post(
            reverse("jobhunt_ai:scout_start"),
            {"location": "Nivelles", "radius_km": 40},
        )
        self.assertEqual(response.status_code, 400)

    def test_scout_launches_run(self):
        make_profile()
        with fakes.fake_llm(), fakes.eager_runs(), mock.patch(
            "jobhunt_ai.scraping.sources.fetch_page_text", return_value="du texte"
        ):
            response = self.client.post(
                reverse("jobhunt_ai:scout_start"),
                {"keywords": "devops", "location": "Nivelles", "radius_km": 40},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AgentRun.objects.get().kind, RunKind.SCOUT)

    def test_invalid_scout_form_rerenders_with_422(self):
        make_profile()
        response = self.client.post(
            reverse("jobhunt_ai:scout_start"),
            {"location": "Nivelles", "radius_km": 9999},
        )
        self.assertEqual(response.status_code, 422)


class LeadTriageTests(PremiumTestCase):
    def make_lead(self, **kwargs) -> OfferLead:
        defaults = {
            "title": "DevOps Engineer",
            "company_name": "Widgets SA",
            "location": "Braine-l'Alleud",
            "url": "https://example.org/jobs/devops",
            "source_name": "ICTjob",
            "description": "Ansible et Linux.",
            "language": "fr",
            "score": 81,
            "score_reason": "Stack alignée.",
        }
        defaults.update(kwargs)
        defaults.setdefault("owner", self.user)
        return OfferLead.objects.create(**defaults)

    def test_someone_else_s_lead_is_a_404(self):
        from accounts.testing import make_user

        lead = self.make_lead(owner=make_user("Marie"))
        for route in ("jobhunt_ai:lead_import", "jobhunt_ai:lead_dismiss", "jobhunt_ai:lead_restore"):
            with self.subTest(route=route):
                self.assertEqual(self.client.post(reverse(route, args=[lead.pk])).status_code, 404)
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.NEW)

    def test_import_creates_backlog_application(self):
        lead = self.make_lead()
        response = self.client.post(reverse("jobhunt_ai:lead_import", args=[lead.pk]))
        self.assertEqual(response.status_code, 200)

        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.IMPORTED)
        application = lead.application
        self.assertIsNotNone(application)
        self.assertEqual(application.status, Status.BACKLOG)
        self.assertEqual(application.company.name, "Widgets SA")
        self.assertEqual(application.score, 81)
        self.assertEqual(application.posting_raw, "Ansible et Linux.")
        # Marqué comme du pré-tri : l'évaluation fine remplacera ce résumé
        # plutôt que de laisser sa justification à côté d'un autre score.
        self.assertEqual(application.summary, f"{LEAD_SUMMARY_PREFIX}Stack alignée.")
        self.assertEqual(application.source_platform, Platform.objects.get(name="ICTjob"))
        self.assertTrue(application.events.filter(title__icontains="copilote").exists())
        self.assertEqual(application.owner, self.user)
        self.assertEqual(application.company.owner, self.user)
        self.assertEqual(application.source_platform.owner, self.user)

    def test_lead_row_shows_the_confidence_and_the_blockers(self):
        """Le pré-tri juge sur un extrait : la page doit le dire, sinon 81 %
        se lit comme un verdict."""
        self.make_lead(score_confidence="faible", score_blockers=["Néerlandais exigé"])
        response = self.client.get(reverse("jobhunt_ai:leads"))
        self.assertContains(response, "pré-tri")
        self.assertContains(response, "fiabilité faible")
        self.assertContains(response, "Néerlandais exigé")

    def test_import_twice_does_not_duplicate(self):
        lead = self.make_lead()
        self.client.post(reverse("jobhunt_ai:lead_import", args=[lead.pk]))
        self.client.post(reverse("jobhunt_ai:lead_import", args=[lead.pk]))
        self.assertEqual(Application.objects.count(), 1)

    def test_dismiss_and_restore(self):
        lead = self.make_lead()
        self.client.post(reverse("jobhunt_ai:lead_dismiss", args=[lead.pk]))
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.DISMISSED)
        self.client.post(reverse("jobhunt_ai:lead_restore", args=[lead.pk]))
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.NEW)


class ProfileManagementTests(PremiumTestCase):
    def test_set_primary_switches_exclusively(self):
        first = make_profile()
        second = CandidateProfile.objects.create(owner=self.user, label="CV anglais")
        response = self.client.post(
            reverse("jobhunt_ai:profile_set_primary", args=[second.pk])
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["HX-Redirect"], reverse("jobhunt_ai:profile"))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_primary)
        self.assertTrue(second.is_primary)

    def test_delete_profile(self):
        profile = make_profile()
        response = self.client.post(
            reverse("jobhunt_ai:profile_delete", args=[profile.pk])
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(CandidateProfile.objects.exists())

    def test_primary_is_exclusive_per_account_only(self):
        from accounts.testing import make_user

        theirs = make_profile(owner=make_user("Marie"), label="Profil de Marie")
        mine = CandidateProfile.objects.create(owner=self.user, label="Le mien", is_primary=True)
        theirs.refresh_from_db()
        self.assertTrue(theirs.is_primary)
        self.assertTrue(mine.is_primary)
        self.assertEqual(CandidateProfile.primary(self.user), mine)
        self.assertEqual(self.client.post(
            reverse("jobhunt_ai:profile_set_primary", args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.client.post(
            reverse("jobhunt_ai:profile_delete", args=[theirs.pk])).status_code, 404)
        self.assertTrue(CandidateProfile.objects.filter(pk=theirs.pk).exists())

    def test_the_contact_line_shows_what_the_account_knows(self):
        """Nom, e-mail, téléphone et point de départ viennent du compte ; le
        téléphone est facultatif et ne laisse pas de séparateur orphelin."""
        from accounts.services import profile_for

        make_profile(full_name="Lionel Test", email="lionel@example.be", location="Nivelles")
        page = self.client.get(reverse("jobhunt_ai:profile"))
        self.assertContains(page, "lionel@example.be")
        self.assertNotContains(page, "· </div>")

        account = profile_for(self.user)
        account.phone = "+32 470 12 34 56"
        account.save(update_fields=["phone"])
        CandidateProfile.objects.update(phone=account.phone)
        page = self.client.get(reverse("jobhunt_ai:profile"))
        self.assertContains(page, "+32 470 12 34 56")

    def test_upload_form_only_lists_the_account_s_documents(self):
        from accounts.testing import make_user
        from tracker.models import Document, DocumentKind

        Document.objects.create(owner=make_user("Marie"), kind=DocumentKind.CV, label="CV de Marie",
                                file=SimpleUploadedFile("marie.txt", b"x"))
        Document.objects.create(owner=self.user, kind=DocumentKind.CV, label="Mon CV",
                                file=SimpleUploadedFile("moi.txt", b"x"))
        response = self.client.get(reverse("jobhunt_ai:profile"))
        self.assertContains(response, "Mon CV")
        self.assertNotContains(response, "CV de Marie")


class ReviewRegressionViewTests(PremiumTestCase):
    """Verrous posés après la revue adversariale du 2026-08-31."""

    def test_free_account_htmx_request_redirects_to_premium_page(self):
        application = make_application()
        Profile.objects.filter(user=self.user).update(subscription_level=SubscriptionLevel.FREE)
        with override_settings(DEBUG=True):
            response = self.client.post(
                reverse("jobhunt_ai:evaluate", args=[application.pk]),
                HTTP_HX_REQUEST="true",
            )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["HX-Redirect"], reverse("jobhunt_ai:copilot"))

    def test_panel_poll_swaps_panel_without_full_refresh(self):
        application = make_application()
        run = AgentRun.objects.create(
            owner=self.user, kind=RunKind.MATCH, status=RunStatus.SUCCEEDED,
            application=application, result={"score": 82},
        )
        response = self.client.get(
            reverse("jobhunt_ai:run_status", args=[run.pk]),
            {"poll": "1", "panel": "1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("HX-Refresh", response.headers)
        self.assertEqual(response.headers["HX-Retarget"], "#ai-panel")
        self.assertEqual(response.headers["HX-Reswap"], "outerHTML")
        self.assertContains(response, 'id="ai-panel"')
        self.assertIn("82", response.headers["HX-Trigger"])

    def test_profile_poll_still_refreshes_page(self):
        run = AgentRun.objects.create(
            owner=self.user, kind=RunKind.PARSE_CV, status=RunStatus.SUCCEEDED,
            result={"skill_count": 3},
        )
        response = self.client.get(
            reverse("jobhunt_ai:run_status", args=[run.pk]), {"poll": "1"}
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["HX-Refresh"], "true")

    def test_evaluate_fragment_polls_with_panel_flag(self):
        make_profile()
        application = make_application()
        existing = AgentRun.objects.create(
            owner=self.user, kind=RunKind.MATCH, status=RunStatus.RUNNING, application=application
        )
        response = self.client.post(reverse("jobhunt_ai:evaluate", args=[application.pk]))
        self.assertContains(response, f"run-{existing.pk}")
        self.assertContains(response, "panel=1")
