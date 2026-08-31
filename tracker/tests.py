"""Tests for the tracker.

Coverage is deliberately weighted towards rendering: most of the risk in an
HTMX app is a template that only breaks once a branch finally has data in it.
"""

from __future__ import annotations

import datetime as dt
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tracker.models import (
    ActivityEvent,
    Application,
    Company,
    Contact,
    Document,
    DocumentKind,
    EventKind,
    GapStatus,
    Language,
    Platform,
    Sector,
    SkillGap,
    Status,
    WorkMode,
)

MEDIA = tempfile.mkdtemp(prefix="jobhunt-tests-")


def make_application(**kwargs) -> Application:
    company = kwargs.pop("company", None) or Company.objects.create(
        name=kwargs.pop("company_name", "Acme"), sector=Sector.PRIVATE
    )
    defaults = {
        "title": "Senior DevOps Engineer",
        "location": "Bruxelles",
        "distance_km": 30,
        "score": 72,
        "cv_language": Language.FR,
        "status": Status.TO_APPLY,
        "summary": "Un résumé.",
        "strengths": "- Linux\n- Ansible",
        "weaknesses": "- Pas de Terraform",
        "strategy": "Joue la carte réseau.",
    }
    defaults.update(kwargs)
    return Application.objects.create(company=company, **defaults)


@override_settings(MEDIA_ROOT=MEDIA)
class PageRenderTests(TestCase):
    """Every page must render with data, and again when the data is empty."""

    @classmethod
    def setUpTestData(cls):
        platform = Platform.objects.create(
            name="LinkedIn", searched_for="DevOps", outcome="Source la plus productive."
        )
        Platform.objects.create(name="Pistes non explorées", outcome="uvcw.be", is_lead=True)
        SkillGap.objects.create(
            name="Terraform", demand_count=6, demand_label="6 offres sur 10",
            why_it_matters="Standard de fait.", action_plan="Reprovisionne le homelab.",
        )
        cls.ready = make_application(company_name="HMS Networks", score=88, distance_km=0)
        cls.ready.source_platform = platform
        cls.ready.save()

        cls.sent = make_application(
            company_name="Haulogy",
            status=Status.SENT,
            applied_on=timezone.localdate() - dt.timedelta(days=30),
            follow_up_on=timezone.localdate() - dt.timedelta(days=3),
        )
        cls.interview = make_application(company_name="Collibra", status=Status.INTERVIEW)
        cls.closed = make_application(
            company_name="Odoo", status=Status.REJECTED, closed_on=timezone.localdate()
        )
        cls.discarded = make_application(
            company_name="Smals",
            status=Status.DISCARDED,
            discard_reason="Poste Windows, et néerlandais exigé.",
        )
        cls.backlog = make_application(company_name="iMio", status=Status.BACKLOG, score=None)

        Contact.objects.create(
            application=cls.sent, name="Marie Dupont", role="Recruteuse",
            email="m@example.com", phone="+32 2 000 00 00",
        )
        ActivityEvent.objects.create(
            application=cls.sent, kind=EventKind.APPLIED, title="Candidature envoyée"
        )
        ActivityEvent.objects.create(
            application=cls.interview,
            kind=EventKind.INTERVIEW,
            title="Entretien technique",
            happened_on=timezone.localdate() + dt.timedelta(days=4),
        )
        Document.objects.create(
            application=cls.ready, kind=DocumentKind.CV, label="CV.docx",
            language=Language.EN, is_primary=True,
            file=SimpleUploadedFile("CV.docx", b"x" * 40),
        )
        Document.objects.create(
            kind=DocumentKind.CV, label="CV_base.docx", language=Language.FR,
            file=SimpleUploadedFile("CV_base.docx", b"y" * 40),
        )

    def test_every_page_renders(self):
        for name, args in [
            ("tracker:dashboard", []),
            ("tracker:pipeline", []),
            ("tracker:application_list", []),
            ("tracker:document_library", []),
            ("tracker:insights", []),
            ("tracker:application_create", []),
            ("tracker:application_detail", [self.ready.pk]),
            ("tracker:application_update", [self.ready.pk]),
        ]:
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 200)

    def test_detail_renders_for_every_status(self):
        for status, _ in Status.choices:
            with self.subTest(status=status):
                self.ready.status = status
                self.ready.save(update_fields=["status"])
                response = self.client.get(self.ready.get_absolute_url())
                self.assertEqual(response.status_code, 200)

    def test_the_favicon_data_uri_carries_usable_colours(self):
        """`urlencode` escapes "#" itself — pre-escaping it yields %2523."""
        import re
        import urllib.parse

        html = self.client.get(reverse("tracker:dashboard")).content.decode()
        href = re.search(r'<link rel="icon" href="([^"]+)"', html).group(1)
        svg = urllib.parse.unquote(href)
        colours = re.findall(r'(?:fill|stroke)="([^"]+)"', svg)
        self.assertTrue(colours)
        for colour in colours:
            self.assertTrue(
                colour == "none" or colour.startswith("#"),
                f"couleur illisible par le moteur SVG : {colour}",
            )

    def test_dashboard_surfaces_what_needs_attention(self):
        response = self.client.get(reverse("tracker:dashboard"))
        self.assertContains(response, "À traiter maintenant")
        self.assertIn(self.sent, response.context["attention"])
        self.assertIn(self.ready, response.context["to_apply"])
        # Import chatter about written-off offers must not crowd the feed.
        self.assertNotIn(self.discarded, [e.application for e in response.context["recent"]])

    def test_pages_render_with_an_empty_database(self):
        Document.objects.all().delete()
        ActivityEvent.objects.all().delete()
        Contact.objects.all().delete()
        Application.objects.all().delete()
        for name in [
            "tracker:dashboard", "tracker:pipeline", "tracker:application_list",
            "tracker:document_library", "tracker:insights",
        ]:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_css_numbers_never_use_a_decimal_comma(self):
        """A French locale would render 75.0 as "75,0" and break the CSS."""
        for name in ["tracker:insights", "tracker:dashboard"]:
            with self.subTest(page=name):
                html = self.client.get(reverse(name)).content.decode()
                for fragment in ["left: ", "width: ", "bottom: "]:
                    for chunk in html.split(fragment)[1:]:
                        value = chunk.split(";")[0].split('"')[0].strip()
                        self.assertNotIn(",", value, f"{fragment}{value}")

    def test_insights_plots_only_fully_measured_offers(self):
        response = self.client.get(reverse("tracker:insights"))
        plotted = [point["application"] for point in response.context["points"]]
        self.assertIn(self.ready, plotted)
        self.assertNotIn(self.backlog, plotted)  # no score
        self.assertNotIn(self.discarded, plotted)


@override_settings(MEDIA_ROOT=MEDIA)
class FilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.open = make_application(company_name="HMS Networks", score=88)
        cls.public = make_application(
            company=Company.objects.create(name="Egov Select", sector=Sector.PUBLIC),
            status=Status.SENT, cv_language=Language.FR, score=68,
        )
        cls.english = make_application(company_name="Collibra", cv_language=Language.EN, score=72)
        cls.discarded = make_application(
            company_name="Smals", status=Status.DISCARDED,
            discard_reason="Stack Windows et néerlandais exigé.",
        )

    def get_list(self, **params):
        return self.client.get(reverse("tracker:application_list"), params)

    def test_default_scope_hides_discarded(self):
        results = self.get_list().context["applications"]
        self.assertNotIn(self.discarded, results)
        self.assertIn(self.open, results)

    def test_discarded_scope(self):
        results = self.get_list(scope="discarded").context["applications"]
        self.assertEqual(list(results), [self.discarded])

    def test_search_reaches_into_the_analysis(self):
        self.open.weaknesses = "- Aucune expérience de production manufacturière"
        self.open.save()
        results = self.get_list(q="manufacturière").context["applications"]
        self.assertEqual(list(results), [self.open])

    def test_search_reaches_discard_reasons(self):
        results = self.get_list(q="néerlandais", scope="all").context["applications"]
        self.assertEqual(list(results), [self.discarded])

    def test_sector_and_language_filters(self):
        self.assertEqual(
            list(self.get_list(sector=Sector.PUBLIC).context["applications"]), [self.public]
        )
        self.assertEqual(
            list(self.get_list(language=Language.EN).context["applications"]), [self.english]
        )

    def test_the_search_box_carries_the_hook_its_trigger_looks_for(self):
        html = self.get_list().content.decode()
        self.assertIn("data-search-input", html)
        self.assertIn("keyup from:[data-search-input]", html)

    def test_htmx_request_returns_only_the_table(self):
        response = self.get_list(**{"HTTP_HX_REQUEST": "true"}) if False else self.client.get(
            reverse("tracker:application_list"), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<html")
        self.assertContains(response, 'id="applications"')

    def test_sort_options_all_execute(self):
        for sort in ["-score", "pipeline", "-applied_on", "follow_up_on", "company__name", "-updated_at"]:
            with self.subTest(sort=sort):
                response = self.get_list(sort=sort)
                self.assertEqual(len(list(response.context["applications"])), 3)


@override_settings(MEDIA_ROOT=MEDIA)
class StatusTransitionTests(TestCase):
    def setUp(self):
        self.application = make_application()

    def test_moving_to_sent_stamps_the_date_and_schedules_a_follow_up(self):
        self.application.apply_status(Status.SENT)
        self.application.refresh_from_db()
        self.assertEqual(self.application.applied_on, timezone.localdate())
        self.assertEqual(
            self.application.follow_up_on,
            timezone.localdate() + dt.timedelta(days=settings.DEFAULT_FOLLOW_UP_DAYS),
        )
        self.assertEqual(self.application.events.first().kind, EventKind.APPLIED)

    def test_closing_clears_the_follow_up_and_stamps_the_close_date(self):
        self.application.apply_status(Status.SENT)
        self.application.apply_status(Status.REJECTED)
        self.application.refresh_from_db()
        self.assertIsNone(self.application.follow_up_on)
        self.assertEqual(self.application.closed_on, timezone.localdate())

    def test_reopening_a_closed_application_clears_the_close_date(self):
        self.application.apply_status(Status.REJECTED)
        self.application.apply_status(Status.SENT)
        self.application.refresh_from_db()
        self.assertIsNone(self.application.closed_on)

    def test_a_no_op_transition_logs_nothing(self):
        self.assertFalse(self.application.apply_status(Status.TO_APPLY))
        self.assertEqual(self.application.events.count(), 0)

    def test_an_existing_applied_date_is_preserved(self):
        earlier = timezone.localdate() - dt.timedelta(days=12)
        self.application.applied_on = earlier
        self.application.apply_status(Status.SENT)
        self.application.refresh_from_db()
        self.assertEqual(self.application.applied_on, earlier)

    def test_staleness_and_follow_up_states(self):
        today = timezone.localdate()
        self.application.status = Status.SENT
        self.application.applied_on = today - dt.timedelta(days=settings.STALE_AFTER_DAYS + 1)
        self.application.follow_up_on = today - dt.timedelta(days=2)
        self.application.save()
        self.assertTrue(self.application.is_stale)
        self.assertEqual(self.application.follow_up_state, "overdue")
        self.assertTrue(self.application.needs_attention)

        self.application.follow_up_on = today
        self.assertEqual(self.application.follow_up_state, "today")
        self.application.follow_up_on = today + dt.timedelta(days=2)
        self.assertEqual(self.application.follow_up_state, "soon")
        self.application.follow_up_on = today + dt.timedelta(days=30)
        self.assertEqual(self.application.follow_up_state, "later")

    def test_score_bands(self):
        for score, band in [(88, "high"), (80, "high"), (72, "mid"), (65, "mid"),
                            (55, "low"), (None, "none")]:
            self.application.score = score
            self.assertEqual(self.application.score_band, band)


@override_settings(MEDIA_ROOT=MEDIA)
class HtmxEndpointTests(TestCase):
    def setUp(self):
        self.application = make_application()
        self.gap = SkillGap.objects.create(name="Terraform", demand_count=6)

    def post(self, route, args=(), **data):
        return self.client.post(reverse(route, args=args), data, HTTP_HX_REQUEST="true")

    def test_set_status_from_each_surface(self):
        for source in ["board", "row", "list", "detail"]:
            with self.subTest(source=source):
                response = self.post(
                    "tracker:set_status", [self.application.pk],
                    status=Status.SENT, source=source,
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("HX-Trigger", response.headers)
                if source == "list":
                    self.assertContains(response, f'id="app-row-{self.application.pk}"')
                self.application.status = Status.TO_APPLY
                self.application.save(update_fields=["status"])

    def test_set_status_rejects_an_unknown_value(self):
        response = self.post("tracker:set_status", [self.application.pk], status="nope")
        self.assertEqual(response.status_code, 400)

    def test_advance_walks_the_pipeline_and_stops_at_the_end(self):
        self.post("tracker:advance_status", [self.application.pk], source="board")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Status.SENT)

        self.application.status = Status.ACCEPTED
        self.application.save(update_fields=["status"])
        response = self.post("tracker:advance_status", [self.application.pk], source="detail")
        self.assertEqual(response.status_code, 200)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Status.ACCEPTED)

    def test_follow_up_presets_and_explicit_dates(self):
        self.application.status = Status.SENT
        self.application.save(update_fields=["status"])

        self.post("tracker:set_follow_up", [self.application.pk], in_days="7")
        self.application.refresh_from_db()
        self.assertEqual(self.application.follow_up_on, timezone.localdate() + dt.timedelta(days=7))

        self.post("tracker:set_follow_up", [self.application.pk], follow_up_on="2026-12-01")
        self.application.refresh_from_db()
        self.assertEqual(self.application.follow_up_on, dt.date(2026, 12, 1))

        self.post("tracker:set_follow_up", [self.application.pk], follow_up_on="")
        self.application.refresh_from_db()
        self.assertIsNone(self.application.follow_up_on)

        self.assertEqual(
            self.post("tracker:set_follow_up", [self.application.pk],
                      follow_up_on="pas une date").status_code,
            400,
        )

    def test_mark_followed_up_logs_and_reschedules(self):
        self.application.status = Status.SENT
        self.application.save(update_fields=["status"])
        self.post("tracker:mark_followed_up", [self.application.pk])
        self.application.refresh_from_db()
        self.assertEqual(self.application.events.first().kind, EventKind.FOLLOW_UP)
        self.assertEqual(
            self.application.follow_up_on,
            timezone.localdate() + dt.timedelta(days=settings.DEFAULT_FOLLOW_UP_DAYS),
        )

    def test_notes_round_trip(self):
        response = self.post("tracker:edit_notes", [self.application.pk],
                             personal_notes="Demander la part réelle de MDM.")
        self.assertEqual(response.status_code, 200)
        self.application.refresh_from_db()
        self.assertEqual(self.application.personal_notes, "Demander la part réelle de MDM.")

    def test_event_add_and_delete(self):
        response = self.post(
            "tracker:add_event", [self.application.pk],
            happened_on="2026-09-02", kind=EventKind.CALL,
            title="Appel du recruteur", detail="20 minutes.",
        )
        self.assertContains(response, "Appel du recruteur")
        event = self.application.events.get()
        response = self.post("tracker:delete_event", [event.pk])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.application.events.count(), 0)

    def test_invalid_event_redisplays_the_panel_with_errors(self):
        response = self.post("tracker:add_event", [self.application.pk], title="", kind="note")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.application.events.count(), 0)

    def test_document_upload_and_delete(self):
        upload = SimpleUploadedFile("CV_Haulogy.docx", b"contenu")
        response = self.client.post(
            reverse("tracker:add_document", args=[self.application.pk]),
            {"file": upload, "kind": DocumentKind.CV, "label": "", "language": Language.FR,
             "is_primary": "on"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        document = self.application.documents.get()
        self.assertEqual(document.label, "CV_Haulogy.docx")
        self.assertTrue(document.is_primary)
        self.assertEqual(self.application.primary_cv, document)

        response = self.post("tracker:delete_document", [document.pk])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.application.documents.count(), 0)

    def test_uploading_a_second_primary_demotes_the_first(self):
        for index in (1, 2):
            self.client.post(
                reverse("tracker:add_document", args=[self.application.pk]),
                {"file": SimpleUploadedFile(f"CV{index}.docx", b"x"),
                 "kind": DocumentKind.CV, "label": "", "is_primary": "on"},
                HTTP_HX_REQUEST="true",
            )
        primaries = self.application.documents.filter(is_primary=True)
        self.assertEqual(primaries.count(), 1)
        self.assertEqual(primaries.get().label, "CV2.docx")

    def test_library_upload_redirects(self):
        response = self.client.post(
            reverse("tracker:add_library_document"),
            {"file": SimpleUploadedFile("CV_base.docx", b"x"), "kind": DocumentKind.CV,
             "label": "CV générique"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["HX-Redirect"], reverse("tracker:document_library"))
        self.assertTrue(Document.objects.filter(application__isnull=True).exists())

    def test_contact_add_and_delete(self):
        response = self.post("tracker:add_contact", [self.application.pk],
                             name="Marie Dupont", role="Recruteuse")
        self.assertContains(response, "Marie Dupont")
        contact = Contact.objects.get()
        self.post("tracker:delete_contact", [contact.pk])
        self.assertEqual(Contact.objects.count(), 0)

    def test_gap_status_toggle(self):
        response = self.post("tracker:set_gap_status", [self.gap.pk], status=GapStatus.DOING)
        self.assertEqual(response.status_code, 200)
        self.gap.refresh_from_db()
        self.assertEqual(self.gap.status, GapStatus.DOING)
        self.assertEqual(
            self.post("tracker:set_gap_status", [self.gap.pk], status="bogus").status_code, 400
        )

    def test_quick_create(self):
        response = self.client.post(
            reverse("tracker:quick_create"),
            {"company_name": "Vertuoza", "title": "Lead Platform Engineer",
             "url": "", "score": 60, "cv_language": Language.FR, "status": Status.BACKLOG},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 204)
        created = Application.objects.get(title="Lead Platform Engineer")
        self.assertEqual(response.headers["HX-Redirect"], created.get_absolute_url())
        self.assertEqual(created.discovered_on, timezone.localdate())

    def test_quick_create_reuses_an_existing_company(self):
        self.client.post(
            reverse("tracker:quick_create"),
            {"company_name": "acme", "title": "Autre poste",
             "cv_language": Language.FR, "status": Status.BACKLOG},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(Company.objects.filter(name__iexact="acme").count(), 1)

    def test_quick_create_rejects_an_empty_form(self):
        response = self.client.post(reverse("tracker:quick_create"), {}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 422)

    def test_get_endpoints_render_their_forms(self):
        for name in ["tracker:quick_create", "tracker:stats_bar"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        for name in ["tracker:edit_notes", "tracker:add_event", "tracker:add_contact",
                     "tracker:add_document"]:
            with self.subTest(endpoint=name):
                response = self.client.get(reverse(name, args=[self.application.pk]))
                self.assertEqual(response.status_code, 200)

    def test_mutating_endpoints_refuse_get(self):
        for name in ["tracker:set_status", "tracker:advance_status", "tracker:set_follow_up"]:
            with self.subTest(endpoint=name):
                response = self.client.get(reverse(name, args=[self.application.pk]))
                self.assertEqual(response.status_code, 405)



@override_settings(MEDIA_ROOT=MEDIA)
class ApplicationFormTests(TestCase):
    def test_create_makes_the_company_when_it_is_new(self):
        response = self.client.post(
            reverse("tracker:application_create"),
            {"company_name": "Nouvelle SPRL", "company_sector": Sector.PRIVATE,
             "title": "Ingénieur système", "status": Status.BACKLOG,
             "cv_language": Language.FR, "work_mode": WorkMode.UNKNOWN},
        )
        self.assertEqual(response.status_code, 302)
        application = Application.objects.get(title="Ingénieur système")
        self.assertEqual(application.company.name, "Nouvelle SPRL")
        self.assertEqual(application.events.count(), 1)

    def test_a_follow_up_cannot_precede_the_application(self):
        response = self.client.post(
            reverse("tracker:application_create"),
            {"company_name": "Acme", "company_sector": Sector.PRIVATE, "title": "Poste",
             "status": Status.SENT, "cv_language": Language.FR, "work_mode": WorkMode.UNKNOWN,
             "applied_on": "2026-08-20", "follow_up_on": "2026-08-10"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("follow_up_on", response.context["form"].errors)

    def test_discarding_requires_a_reason(self):
        response = self.client.post(
            reverse("tracker:application_create"),
            {"company_name": "Acme", "company_sector": Sector.PRIVATE, "title": "Poste",
             "status": Status.DISCARDED, "cv_language": Language.FR,
             "work_mode": WorkMode.UNKNOWN},
        )
        self.assertIn("discard_reason", response.context["form"].errors)

    def test_score_is_bounded(self):
        response = self.client.post(
            reverse("tracker:application_create"),
            {"company_name": "Acme", "company_sector": Sector.PRIVATE, "title": "Poste",
             "status": Status.BACKLOG, "cv_language": Language.FR,
             "work_mode": WorkMode.UNKNOWN, "score": 140},
        )
        self.assertIn("score", response.context["form"].errors)

    def test_update_logs_a_status_change(self):
        application = make_application()
        self.client.post(
            reverse("tracker:application_update", args=[application.pk]),
            {"company_name": application.company.name, "company_sector": Sector.PRIVATE,
             "title": application.title, "status": Status.SENT,
             "cv_language": Language.FR, "work_mode": WorkMode.UNKNOWN},
        )
        application.refresh_from_db()
        self.assertEqual(application.status, Status.SENT)
        self.assertEqual(application.events.filter(kind=EventKind.STATUS).count(), 1)

    def test_delete(self):
        application = make_application()
        response = self.client.post(reverse("tracker:application_delete", args=[application.pk]))
        self.assertRedirects(response, reverse("tracker:application_list"))
        self.assertEqual(Application.objects.count(), 0)


@override_settings(MEDIA_ROOT=MEDIA)
class DocumentStorageTests(TestCase):
    def test_deleting_an_application_removes_its_files_from_disk(self):
        application = make_application()
        document = Document.objects.create(
            application=application, kind=DocumentKind.CV, label="CV.docx",
            file=SimpleUploadedFile("cascade.docx", b"contenu"),
        )
        path = Path(document.file.path)
        self.assertTrue(path.exists())
        application.delete()
        self.assertFalse(path.exists())

    def test_deleting_a_document_removes_its_file(self):
        document = Document.objects.create(
            kind=DocumentKind.CV, label="CV.docx",
            file=SimpleUploadedFile("single.docx", b"contenu"),
        )
        path = Path(document.file.path)
        document.delete()
        self.assertFalse(path.exists())


class SlugTests(TestCase):
    def test_slugs_stay_unique_for_identical_titles(self):
        company = Company.objects.create(name="HMS Networks")
        first = make_application(company=company, title="R&D Firmware Engineer")
        second = Application.objects.create(
            company=company, title="R&D Firmware Engineer", status=Status.BACKLOG
        )
        self.assertNotEqual(first.slug, second.slug)
        self.assertTrue(second.slug.endswith("-2"))

    def test_company_slug_is_generated(self):
        company = Company.objects.create(name="Egov Select (Sûreté de l'État)")
        self.assertEqual(company.slug, "egov-select-surete-de-letat")


class RichTextTests(TestCase):
    def render(self, value):
        from tracker.templatetags.tracker_extras import richtext

        return str(richtext(value))

    def test_bullets_bold_and_links(self):
        html = self.render("- **Python** exigé\n- Voir https://example.com")
        self.assertIn("<ul>", html)
        self.assertIn("<strong>Python</strong>", html)
        self.assertIn('href="https://example.com"', html)

    def test_html_is_escaped(self):
        html = self.render("<script>alert(1)</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_headings_and_empty_input(self):
        self.assertIn("<h3>", self.render("# Titre"))
        self.assertEqual(self.render(""), "")
        self.assertEqual(self.render(None), "")

    def test_french_pluralisation(self):
        from tracker.templatetags.tracker_extras import pluralize_fr

        self.assertEqual(pluralize_fr(0), "")
        self.assertEqual(pluralize_fr(1), "")
        self.assertEqual(pluralize_fr(2), "s")
        self.assertEqual(pluralize_fr(2, "est,sont"), "sont")
        self.assertEqual(pluralize_fr(1, "est,sont"), "est")


class WorkModeDetectionTests(TestCase):
    def detect(self, text):
        from tracker.management.commands.import_legacy import guess_work_mode

        return guess_work_mode(text)

    def test_negated_remote_reads_as_on_site(self):
        self.assertEqual(
            self.detect("Bruxelles — 5 jours sur site, pas de télétravail"), WorkMode.ON_SITE
        )

    def test_plain_remote(self):
        self.assertEqual(self.detect("Bruxelles / Remote Europe"), WorkMode.REMOTE)

    def test_on_site(self):
        self.assertEqual(self.detect("Nivelles — sur place"), WorkMode.ON_SITE)

    def test_unknown(self):
        self.assertEqual(self.detect("Bruxelles"), WorkMode.UNKNOWN)

    def test_hybrid(self):
        self.assertEqual(self.detect("Hybride, 2 jours à Nivelles"), WorkMode.HYBRID)


@override_settings(MEDIA_ROOT=MEDIA)
class ImportCommandTests(TestCase):
    """Runs against the real workbook and folders when they are present."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workbook = Path(settings.LEGACY_WORKBOOK)

    def setUp(self):
        if not self.workbook.exists():
            self.skipTest("Le classeur d'origine n'est pas disponible.")

    def test_import_is_complete_and_idempotent(self):
        call_command("import_legacy", verbosity=0)
        first = {
            "applications": Application.objects.count(),
            "companies": Company.objects.count(),
            "documents": Document.objects.count(),
            "platforms": Platform.objects.count(),
            "gaps": SkillGap.objects.count(),
        }
        self.assertEqual(first["applications"], 24)
        self.assertEqual(Application.objects.discarded().count(), 14)
        self.assertEqual(first["gaps"], 7)

        call_command("import_legacy", verbosity=0)
        self.assertEqual(Application.objects.count(), first["applications"])
        self.assertEqual(Company.objects.count(), first["companies"])
        self.assertEqual(Document.objects.count(), first["documents"])

    def test_every_offer_folder_is_matched_and_parsed(self):
        call_command("import_legacy", verbosity=0)
        folders = [
            path for path in (Path(settings.LEGACY_ROOT) / "Offres").iterdir() if path.is_dir()
        ]
        self.assertEqual(
            Application.objects.exclude(legacy_folder="").count(), len(folders)
        )
        for application in Application.objects.exclude(legacy_folder=""):
            with self.subTest(folder=application.legacy_folder):
                self.assertTrue(application.strengths)
                self.assertTrue(application.weaknesses)
                self.assertTrue(application.strategy)
                self.assertTrue(application.posting_raw)
                self.assertEqual(
                    application.documents.filter(kind=DocumentKind.CV, is_primary=True).count(), 1
                )

    def test_skip_files_leaves_the_media_root_alone(self):
        call_command("import_legacy", "--skip-files", verbosity=0)
        self.assertEqual(Document.objects.count(), 0)
        self.assertTrue(Application.objects.exists())

    def test_import_survives_a_missing_workbook(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("import_legacy", workbook="/nowhere/absent.xlsx", verbosity=0)


def tearDownModule():
    shutil.rmtree(MEDIA, ignore_errors=True)
