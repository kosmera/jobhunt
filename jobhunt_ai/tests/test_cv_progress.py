"""Owner-scoped onboarding progress without raw CV text or provider errors."""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.testing import make_user
from jobhunt_ai.hooks import cv_analysis_status
from jobhunt_ai.models import AgentRun, CandidateProfile, RunKind, RunStatus
from jobhunt_ai.services import llm, runner
from jobhunt_ai.tests import fakes
from jobhunt_ai.tests.test_agents import ingest_cv_document, make_application, make_cv_document
from tracker.models import Document


@override_settings(STORAGES=fakes.REMOTE_STORAGES)
class CVAnalysisProgressTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_progress_is_visible_during_ai_extraction_then_returns_extracted_details(self):
        intake, run = ingest_cv_document(self.user)
        assert run is not None
        pending = cv_analysis_status(self.user, document_id=intake.document.pk)
        self.assertEqual(pending, {
            "status": RunStatus.PENDING, "phase": "", "progress_current": 0, "progress_total": None,
        })

        def while_parsing(*args, **kwargs):
            current = cv_analysis_status(self.user, document_id=intake.document.pk)
            assert current is not None
            self.assertEqual(current["status"], RunStatus.RUNNING)
            self.assertEqual(current["phase"], "Extraction des compétences et du parcours")
            self.assertEqual(current["progress_current"], 1)
            self.assertEqual(current["progress_total"], 3)
            self.assertNotIn("summary", current)
            return llm.StructuredResult(data=fakes.PARSED_PROFILE, input_tokens=100, output_tokens=50)

        with patch.object(llm, "parse_structured", side_effect=while_parsing):
            runner.execute(run.pk, self.user.pk)
        complete = cv_analysis_status(self.user, document_id=intake.document.pk)
        self.assertEqual(complete, {
            "status": RunStatus.SUCCEEDED,
            "phase": "Analyse terminée",
            "progress_current": 3, "progress_total": 3,
            "summary": fakes.PARSED_PROFILE.summary,
            "skills": ["Ansible", "Linux", "Python"],
            "languages": [language.model_dump() for language in fakes.PARSED_PROFILE.languages],
            "experiences": [
                {**experience.model_dump(), "missing_fields": []}
                for experience in fakes.PARSED_PROFILE.experiences
            ],
            "experience_count": 1,
            "incomplete_experience_count": 0,
            "missing_experiences": False,
            "education_count": 1,
        })

    def test_success_reports_missing_experience_details_without_changing_stored_data(self):
        document = make_cv_document(owner=self.user)
        experiences = [
            {"title": "Ingénieur", "company": "Example Employer", "start": "2024", "end": "", "current": True},
            {"title": "Testeur QA", "company": "Example Employer", "start": " \n ", "end": "", "current": False},
            {"title": " ", "company": "", "start": "2018", "end": "2020", "current": False},
            {"title": "Développeur", "company": "Example Employer", "start": "2016", "end": "2018", "current": False},
            {},
        ]
        profile = CandidateProfile.objects.create(
            owner=self.user, source_document=document, experiences=experiences,
        )
        AgentRun.objects.create(
            owner=self.user, kind=RunKind.PARSE_CV, status=RunStatus.SUCCEEDED,
            params={"document_id": document.pk}, result={"profile_id": profile.pk},
        )

        status = cv_analysis_status(self.user, document_id=document.pk)

        assert status is not None
        self.assertEqual(status["status"], RunStatus.SUCCEEDED)
        self.assertEqual(status["experience_count"], 5)
        self.assertEqual(status["incomplete_experience_count"], 3)
        self.assertFalse(status["missing_experiences"])
        self.assertEqual([experience["missing_fields"] for experience in status["experiences"]], [
            [], ["date de début", "date de fin"], ["intitulé du poste", "entreprise"], [],
            ["intitulé du poste", "entreprise", "date de début", "date de fin"],
        ])
        profile.refresh_from_db()
        self.assertEqual(profile.experiences, experiences)

    def test_success_with_no_experiences_reports_the_empty_section(self):
        document = make_cv_document(owner=self.user)
        profile = CandidateProfile.objects.create(owner=self.user, source_document=document)
        AgentRun.objects.create(
            owner=self.user, kind=RunKind.PARSE_CV, status=RunStatus.SUCCEEDED,
            params={"document_id": document.pk}, result={"profile_id": profile.pk},
        )

        status = cv_analysis_status(self.user, document_id=document.pk)

        assert status is not None
        self.assertEqual(status["experiences"], [])
        self.assertEqual(status["experience_count"], 0)
        self.assertEqual(status["incomplete_experience_count"], 0)
        self.assertTrue(status["missing_experiences"])

    def test_status_requires_the_owners_cv(self):
        intake, _ = ingest_cv_document(self.user)
        self.assertIsNone(cv_analysis_status(make_user(), document_id=intake.document.pk))
        self.assertIsNone(cv_analysis_status(self.user, document_id=intake.document.pk + 1))

    def test_languages_preserve_stated_levels_and_leave_unknown_levels_blank(self):
        document = make_cv_document(owner=self.user)
        languages = [
            {"name": "Français", "level": "Langue maternelle"},
            {"name": "Anglais", "level": "C1"},
            {"name": "Néerlandais", "level": "Notions"},
            {"name": " Espagnol ", "level": " \n "},
        ]
        profile = CandidateProfile.objects.create(
            owner=self.user, source_document=document, languages=languages,
        )
        AgentRun.objects.create(
            owner=self.user, kind=RunKind.PARSE_CV, status=RunStatus.SUCCEEDED,
            params={"document_id": document.pk}, result={"profile_id": profile.pk},
        )

        status = cv_analysis_status(self.user, document_id=document.pk)

        assert status is not None
        self.assertEqual(status["languages"], languages[:3] + [{"name": "Espagnol", "level": ""}])
        profile.refresh_from_db()
        self.assertEqual(profile.languages, languages)

    def test_replacement_cv_stays_primary_when_older_analysis_finishes_last(self):
        first, first_run = ingest_cv_document(self.user)
        replacement, replacement_run = ingest_cv_document(self.user)
        assert first_run is not None and replacement_run is not None
        Document.objects.filter(pk=first.document.pk).update(is_primary=False)
        Document.objects.filter(pk=replacement.document.pk).update(is_primary=True)
        with fakes.fake_llm():
            runner.execute(replacement_run.pk, self.user.pk)
            runner.execute(first_run.pk, self.user.pk)
        self.assertEqual(
            CandidateProfile.objects.get(owner=self.user, is_primary=True).source_document,
            replacement.document,
        )

    def test_latest_analysis_failure_does_not_show_an_older_profile_or_raw_error(self):
        with fakes.fake_llm(), fakes.eager_runs():
            intake, _ = ingest_cv_document(self.user)
        AgentRun.objects.create(
            owner=self.user, kind=RunKind.PARSE_CV, status=RunStatus.FAILED,
            params={"document_id": intake.document.pk, "text": "private CV text"},
            error="provider private failure detail",
        )
        self.assertEqual(cv_analysis_status(self.user, document_id=intake.document.pk), {
            "status": RunStatus.FAILED, "phase": "", "progress_current": 0, "progress_total": None,
        })

    def test_application_primary_cv_does_not_replace_the_library_profile(self):
        with fakes.fake_llm(), fakes.eager_runs():
            base, _ = ingest_cv_document(self.user)
        tailored, run = ingest_cv_document(self.user)
        assert run is not None
        Document.objects.filter(pk=tailored.document.pk).update(
            application=make_application(owner=self.user), is_primary=True,
        )
        with fakes.fake_llm():
            runner.execute(run.pk, self.user.pk)
        self.assertEqual(
            CandidateProfile.objects.get(owner=self.user, is_primary=True).source_document,
            base.document,
        )

    def test_expired_queue_is_reported_as_failed(self):
        intake, run = ingest_cv_document(self.user)
        assert run is not None
        AgentRun.objects.filter(pk=run.pk).update(deadline_at=timezone.now() - timedelta(seconds=1))
        status = cv_analysis_status(self.user, document_id=intake.document.pk)
        assert status is not None
        self.assertEqual(status["status"], RunStatus.FAILED)
        self.assertNotIn("error", status)

    def test_success_requires_profile_linked_to_the_same_owner_and_document(self):
        document = make_cv_document(owner=self.user)
        other_document = make_cv_document(owner=self.user)
        other_user = make_user()
        for owner, source_document in ((other_user, document), (self.user, other_document)):
            with self.subTest(owner=owner.pk, document=source_document.pk):
                profile = CandidateProfile.objects.create(
                    owner=owner, source_document=source_document, summary="unrelated result",
                )
                AgentRun.objects.create(
                    owner=self.user, kind=RunKind.PARSE_CV, status=RunStatus.SUCCEEDED,
                    params={"document_id": document.pk}, result={"profile_id": profile.pk},
                )
                self.assertEqual(cv_analysis_status(self.user, document_id=document.pk), {
                    "status": RunStatus.FAILED, "phase": "",
                })
