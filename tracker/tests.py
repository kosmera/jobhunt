"""Tests for the tracker.

Coverage is deliberately weighted towards rendering: most of the risk in an
HTMX app is a template that only breaks once a branch finally has data in it.
"""

from __future__ import annotations

import datetime as dt
import re
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import (
    RequestFactory,
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

import tracker
from accounts.services import LOCAL_USERNAME, preferences_for, profile_for
from accounts.testing import OwnedTestCase, make_user
from tracker import domain, queries, services
from tracker.adapters import persistence
from tracker.adapters.django_orm import DjangoPersistence
from tracker.adapters.memory import MemoryPersistence
from tracker.models import (
    PIPELINE_STATUSES,
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
from tracker.ports import NotFound

MEDIA = tempfile.mkdtemp(prefix="jobhunt-tests-")


def default_owner():
    """The test's signed-in profile — or a fresh one for model-level tests."""
    return get_user_model().objects.order_by("pk").first() or make_user()


def make_company(**kwargs) -> Company:
    kwargs.setdefault("owner", default_owner())
    kwargs.setdefault("sector", Sector.PRIVATE)
    return Company.objects.create(**kwargs)


def make_platform(**kwargs) -> Platform:
    kwargs.setdefault("owner", default_owner())
    return Platform.objects.create(**kwargs)


def make_gap(**kwargs) -> SkillGap:
    kwargs.setdefault("owner", default_owner())
    return SkillGap.objects.create(**kwargs)


def make_document(**kwargs) -> Document:
    application = kwargs.get("application")
    kwargs.setdefault("owner", application.owner if application else default_owner())
    return Document.objects.create(**kwargs)


def make_application(**kwargs) -> Application:
    company = kwargs.pop("company", None)
    owner = kwargs.pop("owner", None) or (company.owner if company else default_owner())
    company = company or make_company(owner=owner, name=kwargs.pop("company_name", "Acme"))
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
    return Application.objects.create(owner=owner, company=company, **defaults)


@override_settings(MEDIA_ROOT=MEDIA)
class PageRenderTests(OwnedTestCase):
    """Every page must render with data, and again when the data is empty."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        platform = make_platform(
            name="LinkedIn", searched_for="DevOps", outcome="Source la plus productive."
        )
        make_platform(name="Pistes non explorées", outcome="uvcw.be", is_lead=True)
        make_gap(
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
        make_document(
            application=cls.ready, kind=DocumentKind.CV, label="CV.docx",
            language=Language.EN, is_primary=True,
            file=SimpleUploadedFile("CV.docx", b"x" * 40),
        )
        make_document(
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
            ("accounts:settings", []),
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

    def test_every_sprite_icon_is_a_symbol_with_a_viewbox(self):
        """<g> cannot carry a viewBox, so a 24x24 icon would be clipped to 16px."""
        import re

        html = self.client.get(reverse("tracker:dashboard")).content.decode()
        # Le cœur et chaque extension apportent leur propre sprite.
        sprites = re.findall(r"(<svg width=\"0\".*?</svg>)", html, re.S)
        self.assertTrue(sprites, "aucun sprite trouvé dans la page")

        definitions = re.findall(r'<(\w+) id="(i-[a-z-]+)"([^>]*)>', "".join(sprites))
        self.assertTrue(definitions, "aucune icône trouvée dans le sprite")
        for tag, name, attrs in definitions:
            with self.subTest(icon=name):
                self.assertEqual(tag, "symbol", f"{name} est un <{tag}>, pas un <symbol>")
                self.assertIn("viewBox", attrs, f"{name} n'a pas de viewBox")

        # Every icon referenced anywhere must exist in the sprite.
        referenced = set(re.findall(r'<use href="#(i-[a-z-]+)"', html))
        defined = {name for _, name, _ in definitions}
        self.assertFalse(referenced - defined, f"icônes manquantes : {referenced - defined}")

    def test_no_svg_relies_on_the_hidden_attribute(self):
        """`hidden` is an HTMLElement property — on an <svg> it does nothing."""
        import re

        for name in ["tracker:dashboard", "tracker:application_list"]:
            html = self.client.get(reverse(name)).content.decode()
            for tag in re.findall(r"<svg[^>]*>", html):
                with self.subTest(page=name, tag=tag[:60]):
                    self.assertNotRegex(
                        tag,
                        r"(?<!aria-)\bhidden\b(?!-)",
                        "un <svg> masqué par l'attribut hidden reste visible",
                    )

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
class FilterTests(OwnedTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.open = make_application(company_name="HMS Networks", score=88)
        cls.public = make_application(
            company=make_company(name="Egov Select", sector=Sector.PUBLIC),
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
        self.preferences = preferences_for(self.application.owner)

    def test_moving_to_sent_stamps_the_date_and_schedules_a_follow_up(self):
        self.application.apply_status(Status.SENT)
        self.application.refresh_from_db()
        self.assertEqual(self.application.applied_on, timezone.localdate())
        self.assertEqual(
            self.application.follow_up_on,
            timezone.localdate() + dt.timedelta(days=self.preferences.follow_up_days),
        )
        self.assertEqual(self.application.events.first().kind, EventKind.APPLIED)

    def test_the_follow_up_delay_is_the_owner_s(self):
        self.preferences.follow_up_days = 3
        self.preferences.save()
        application = Application.objects.with_related().get(pk=self.application.pk)
        application.apply_status(Status.SENT)
        self.assertEqual(application.follow_up_on, timezone.localdate() + dt.timedelta(days=3))

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
        self.application.applied_on = today - dt.timedelta(days=self.preferences.stale_after_days + 1)
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

    def test_staleness_threshold_is_per_profile(self):
        today = timezone.localdate()
        self.application.status = Status.SENT
        self.application.applied_on = today - dt.timedelta(days=6)
        self.application.save()
        self.assertFalse(self.application.is_stale)  # default 14 days
        self.preferences.stale_after_days = 5
        self.preferences.save()
        reloaded = Application.objects.with_related().get(pk=self.application.pk)
        self.assertTrue(reloaded.is_stale)
        self.assertIn(reloaded, Application.objects.for_user(reloaded.owner).stale(days=5))
        self.assertNotIn(reloaded, Application.objects.for_user(reloaded.owner).stale(days=14))
        self.assertIn(reloaded, Application.objects.needs_attention(days=5))

    def test_application_and_company_must_share_an_owner(self):
        other = make_user("Marie")
        with self.assertRaises(ValueError):
            Application.objects.create(
                owner=other, company=self.application.company, title="Intrus"
            )

    def test_attached_document_inherits_the_application_owner(self):
        document = Document.objects.create(
            application=self.application, kind=DocumentKind.CV, label="CV",
            file=SimpleUploadedFile("cv.docx", b"x"),
        )
        self.assertEqual(document.owner, self.application.owner)
        with self.assertRaises(ValueError):
            Document.objects.create(
                owner=make_user("Marie"), application=self.application, kind=DocumentKind.CV,
                label="CV", file=SimpleUploadedFile("cv2.docx", b"x"),
            )


@override_settings(MEDIA_ROOT=MEDIA)
class HtmxEndpointTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.application = make_application()
        self.gap = make_gap(name="Terraform", demand_count=6)

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
        preferences = preferences_for(self.user)
        preferences.follow_up_days = 4
        preferences.save()
        self.post("tracker:mark_followed_up", [self.application.pk])
        self.application.refresh_from_db()
        self.assertEqual(self.application.events.first().kind, EventKind.FOLLOW_UP)
        self.assertEqual(
            self.application.follow_up_on, timezone.localdate() + dt.timedelta(days=4)
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
        library = Document.objects.get(application__isnull=True)
        self.assertEqual(library.owner, self.user)

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
class ApplicationFormTests(OwnedTestCase):
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
        self.assertEqual(application.company.owner, self.user)
        self.assertEqual(application.owner, self.user)
        self.assertEqual(application.events.count(), 1)

    def test_the_same_company_name_can_exist_for_two_profiles(self):
        other = make_user("Marie")
        make_company(owner=other, name="Nouvelle SPRL")
        self.client.post(
            reverse("tracker:application_create"),
            {"company_name": "nouvelle sprl", "company_sector": Sector.PRIVATE,
             "title": "Poste", "status": Status.BACKLOG,
             "cv_language": Language.FR, "work_mode": WorkMode.UNKNOWN},
        )
        self.assertEqual(Company.objects.filter(name__iexact="nouvelle sprl").count(), 2)
        self.assertEqual(Application.objects.get().company.owner, self.user)

    def test_the_distance_help_names_the_home_base(self):
        response = self.client.get(reverse("tracker:application_create"))
        self.assertContains(response, "oiseau depuis Nivelles.")

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
        document = make_document(
            application=application, kind=DocumentKind.CV, label="CV.docx",
            file=SimpleUploadedFile("cascade.docx", b"contenu"),
        )
        path = Path(document.file.path)
        self.assertTrue(path.exists())
        application.delete()
        self.assertFalse(path.exists())

    def test_deleting_a_document_removes_its_file(self):
        document = make_document(
            kind=DocumentKind.CV, label="CV.docx",
            file=SimpleUploadedFile("single.docx", b"contenu"),
        )
        path = Path(document.file.path)
        document.delete()
        self.assertFalse(path.exists())


@override_settings(MEDIA_ROOT=MEDIA)
class DocumentDownloadTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.document = make_document(
            kind=DocumentKind.CV, label="CV base",
            file=SimpleUploadedFile("cv-base.docx", b"contenu"),
        )

    def test_owner_downloads_as_attachment(self):
        response = self.client.get(self.document.get_download_url())
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("cv-base", response.headers["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"contenu")

    def test_pages_link_the_download_view_not_the_media_path(self):
        response = self.client.get(reverse("tracker:document_library"))
        self.assertContains(response, self.document.get_download_url())
        self.assertNotContains(response, self.document.file.url)

    def test_media_paths_are_not_served(self):
        """Also under DEBUG: the old ``static(MEDIA_URL)`` mount only existed
        there, and the test runner forces DEBUG off."""
        import importlib

        from django.urls import Resolver404, clear_url_caches, resolve

        import jobhunt.urls

        self.assertEqual(self.client.get(self.document.file.url).status_code, 404)
        try:
            with override_settings(DEBUG=True):
                importlib.reload(jobhunt.urls)
                clear_url_caches()
                with self.assertRaises(Resolver404):
                    resolve(self.document.file.url)
                self.assertEqual(self.client.get(self.document.file.url).status_code, 404)
        finally:
            importlib.reload(jobhunt.urls)
            clear_url_caches()

    def test_reassigning_an_application_carries_its_documents(self):
        other = make_user("Marie")
        application = make_application()
        attached = make_document(
            application=application, kind=DocumentKind.CV, label="CV",
            file=SimpleUploadedFile("attached.docx", b"x"),
        )
        application = Application.objects.get(pk=application.pk)
        application.owner = other
        application.company = make_company(owner=other, name="Acme bis")
        application.save()
        attached.refresh_from_db()
        self.assertEqual(attached.owner, other)

    def test_someone_else_s_document_is_a_404(self):
        self.client.force_login(make_user("Marie"))
        self.assertEqual(self.client.get(self.document.get_download_url()).status_code, 404)

    def test_missing_file_is_a_404(self):
        Path(self.document.file.path).unlink()
        self.assertEqual(self.client.get(self.document.get_download_url()).status_code, 404)


class SlugTests(TestCase):
    def test_slugs_stay_unique_for_identical_titles(self):
        company = make_company(name="HMS Networks")
        first = make_application(company=company, title="R&D Firmware Engineer")
        second = Application.objects.create(
            owner=company.owner, company=company, title="R&D Firmware Engineer",
            status=Status.BACKLOG,
        )
        self.assertNotEqual(first.slug, second.slug)
        self.assertTrue(second.slug.endswith("-2"))

    def test_company_slug_is_generated(self):
        company = make_company(name="Egov Select (Sûreté de l'État)")
        self.assertEqual(company.slug, "egov-select-surete-de-letat")


@override_settings(MEDIA_ROOT=MEDIA)
class IsolationTests(OwnedTestCase):
    """Signed in as Lionel, every URL that names one of Marie's rows is a 404
    and leaves the row exactly as it was."""

    def setUp(self):
        super().setUp()
        self.other = make_user("Marie")
        self.theirs = make_application(owner=self.other, company_name="Widgets", status=Status.TO_APPLY)
        self.event = ActivityEvent.objects.create(application=self.theirs, title="Note")
        self.contact = Contact.objects.create(application=self.theirs, name="Quelqu'un")
        self.document = make_document(
            application=self.theirs, kind=DocumentKind.CV, label="CV",
            file=SimpleUploadedFile("theirs.docx", b"x"),
        )
        self.gap = make_gap(owner=self.other, name="Cobol")
        make_application(status=Status.SENT)  # Lionel's own, for the list views

    def test_lists_never_show_another_profile(self):
        for name in ["tracker:dashboard", "tracker:pipeline", "tracker:application_list",
                     "tracker:document_library", "tracker:insights"]:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "Widgets")
                self.assertNotContains(response, "Cobol")
        self.assertEqual(self.client.get(reverse("tracker:dashboard")).context["nav_counters"]["tracked"], 1)

    def test_every_row_endpoint_is_a_404_for_another_profile(self):
        application = self.theirs.pk
        attempts = [
            ("get", "tracker:application_detail", [application], {}),
            ("get", "tracker:application_update", [application], {}),
            ("post", "tracker:application_update", [application], {"title": "Pirate"}),
            ("post", "tracker:application_delete", [application], {}),
            ("post", "tracker:set_status", [application], {"status": Status.SENT}),
            ("post", "tracker:advance_status", [application], {}),
            ("post", "tracker:set_follow_up", [application], {"in_days": "7"}),
            ("post", "tracker:mark_followed_up", [application], {}),
            ("post", "tracker:edit_notes", [application], {"personal_notes": "Pirate"}),
            ("get", "tracker:edit_notes", [application], {}),
            ("post", "tracker:add_event", [application], {"happened_on": "2026-09-01", "kind": "note", "title": "Pirate"}),
            ("post", "tracker:add_contact", [application], {"name": "Pirate"}),
            ("get", "tracker:add_document", [application], {}),
            ("post", "tracker:delete_event", [self.event.pk], {}),
            ("post", "tracker:delete_contact", [self.contact.pk], {}),
            ("post", "tracker:delete_document", [self.document.pk], {}),
            ("get", "tracker:document_download", [self.document.pk], {}),
            ("post", "tracker:set_gap_status", [self.gap.pk], {"status": GapStatus.DONE}),
        ]
        for method, name, args, data in attempts:
            with self.subTest(endpoint=name, method=method):
                call = getattr(self.client, method)
                response = call(reverse(name, args=args), data, HTTP_HX_REQUEST="true")
                self.assertEqual(response.status_code, 404)

        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.status, Status.TO_APPLY)
        self.assertEqual(self.theirs.title, "Senior DevOps Engineer")
        self.assertEqual(self.theirs.personal_notes, "")
        self.assertIsNone(self.theirs.follow_up_on)
        self.assertEqual(self.theirs.events.count(), 1)
        self.assertEqual(self.theirs.contacts.count(), 1)
        self.assertEqual(self.theirs.documents.count(), 1)
        self.gap.refresh_from_db()
        self.assertEqual(self.gap.status, GapStatus.TODO)

    def test_forms_only_offer_the_profile_s_own_reference_data(self):
        make_platform(owner=self.other, name="Leur plateforme")
        response = self.client.get(reverse("tracker:application_create"))
        self.assertNotContains(response, "Leur plateforme")
        self.assertNotContains(response, "Widgets")  # company datalist

    def test_a_company_from_another_profile_cannot_be_borrowed(self):
        """The form names companies, never their ids, so the worst case is a
        namesake created on Lionel's side."""
        self.client.post(
            reverse("tracker:application_create"),
            {"company_name": "Widgets", "company_sector": Sector.PRIVATE, "title": "Poste",
             "status": Status.BACKLOG, "cv_language": Language.FR, "work_mode": WorkMode.UNKNOWN},
        )
        mine = Application.objects.get(owner=self.user, title="Poste")
        self.assertNotEqual(mine.company, self.theirs.company)
        self.assertEqual(mine.company.owner, self.user)


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
class ImportCommandTests(OwnedTestCase):
    """Runs against the real workbook and folders when they are present."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workbook = Path(settings.LEGACY_WORKBOOK)

    def setUp(self):
        super().setUp()
        if not self.workbook.exists():
            self.skipTest("Le classeur d'origine n'est pas disponible.")

    def test_everything_imported_belongs_to_the_profile(self):
        call_command("import_legacy", "--skip-files", verbosity=0)
        for model in (Application, Company, Platform, SkillGap):
            with self.subTest(model=model.__name__):
                self.assertFalse(model.objects.exclude(owner=self.user).exists())

    def test_user_option_and_ambiguity(self):
        from django.core.management.base import CommandError

        make_user("Marie", username="marie")
        with self.assertRaises(CommandError):
            call_command("import_legacy", "--skip-files", verbosity=0)
        with self.assertRaises(CommandError):
            call_command("import_legacy", "--skip-files", user="personne", verbosity=0)
        call_command("import_legacy", "--skip-files", user="marie", verbosity=0)
        self.assertTrue(Application.objects.filter(owner__username="marie").exists())
        self.assertFalse(Application.objects.filter(owner=self.user).exists())

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


class PluginFrameworkTests(TestCase):
    """Le cœur doit fonctionner avec zéro, une ou une extension cassée."""

    def _with_entry_points(self, entries):
        """Fait tourner la découverte sur une liste de points d'entrée factices."""
        from unittest import mock

        from jobhunt import plugins

        patcher = mock.patch.object(plugins, "entry_points", return_value=entries)
        patcher.start()
        self.addCleanup(patcher.stop)
        plugins.get_plugins.cache_clear()
        self.addCleanup(plugins.get_plugins.cache_clear)
        return plugins

    def test_no_plugins_yields_empty_registry(self):
        plugins = self._with_entry_points([])
        request = RequestFactory().get("/")
        request.user = make_user("Lionel")
        self.assertEqual(plugins.get_plugins(), ())
        self.assertEqual(plugins.plugin_apps(), [])
        self.assertEqual(plugins.plugin_nav_items(), [])
        self.assertEqual(plugins.plugin_nav_badges(request), {})
        self.assertEqual(plugins.plugin_templates("icon_templates"), [])

    def test_broken_plugin_is_skipped(self):
        from unittest import mock

        broken = mock.Mock()
        broken.name = "casse"
        broken.load.side_effect = ImportError("paquet manquant")
        plugins = self._with_entry_points([broken])
        self.assertEqual(plugins.get_plugins(), ())

    def test_declared_plugin_is_exposed(self):
        from unittest import mock

        descriptor = mock.Mock()
        descriptor.app = "exemple.apps.ExempleConfig"
        descriptor.nav_items = [("exemple:index", "Exemple", "gauge", None)]
        descriptor.nav_badges = ""
        descriptor.icon_templates = ["exemple/icons.html"]
        descriptor.application_panels = []
        entry = mock.Mock()
        entry.name = "exemple"
        entry.load.return_value = descriptor
        plugins = self._with_entry_points([entry])
        self.assertEqual(plugins.plugin_apps(), ["exemple.apps.ExempleConfig"])
        self.assertEqual(len(plugins.plugin_nav_items()), 1)
        self.assertEqual(plugins.plugin_templates("icon_templates"), ["exemple/icons.html"])

    def test_navigation_pages_render_without_plugins(self):
        """Les pages du cœur ne dépendent d'aucune extension."""
        from unittest import mock

        self.client.force_login(make_user("Lionel"))

        for function in ("plugin_nav_items", "plugin_nav_badges"):
            patcher = mock.patch(
                f"tracker.context_processors.{function}",
                return_value=[] if function == "plugin_nav_items" else {},
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch("tracker.context_processors.plugin_templates", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

        response = self.client.get(reverse("tracker:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["plugin_icon_templates"], [])


class AutoMigrateRunserverTests(TestCase):
    """``runserver`` writes and applies migrations in development, warns elsewhere."""

    PLAN = [(mock.Mock(app_label="tracker"), False), (mock.Mock(app_label="jobhunt_ai"), False)]

    def run_check(self, *, auto_migrate: bool = True, plan=(), changes=None):
        """Run ``check_migrations`` with the plan and the detected changes
        stubbed; returns the ``call_command`` mock and what was printed."""
        from io import StringIO

        from tracker.management.commands.runserver import Command

        self.enterContext(override_settings(AUTO_MIGRATE=auto_migrate))
        self.enterContext(
            mock.patch(
                "django.db.migrations.executor.MigrationExecutor.migration_plan",
                return_value=list(plan),
            )
        )
        if changes is not None:
            outcome = (
                {"side_effect": changes} if isinstance(changes, Exception) else {"return_value": changes}
            )
            self.enterContext(
                mock.patch(
                    "django.db.migrations.autodetector.MigrationAutodetector.changes", **outcome
                )
            )
        call = self.enterContext(mock.patch("tracker.management.commands.runserver.call_command"))
        command = Command(stdout=StringIO())
        command.check_migrations()
        return call, command.stdout.getvalue()

    def test_runserver_resolves_to_the_override(self):
        from django.core.management import get_commands

        self.assertEqual(get_commands()["runserver"], "tracker")

    def test_pending_migrations_are_applied_in_development(self):
        call, output = self.run_check(plan=self.PLAN, changes={})
        call.assert_called_once()
        self.assertEqual(call.call_args.args[0], "migrate")
        self.assertFalse(call.call_args.kwargs["interactive"])
        self.assertIn("2 migration(s) en attente (jobhunt_ai, tracker)", output)

    def test_outside_development_django_only_warns(self):
        call, output = self.run_check(auto_migrate=False, plan=self.PLAN, changes={})
        call.assert_not_called()
        self.assertIn("unapplied migration", output)

    def test_nothing_to_do_stays_quiet(self):
        call, output = self.run_check(plan=[], changes={})
        call.assert_not_called()
        self.assertEqual(output, "")

    def test_model_changes_get_their_migration_before_migrate(self):
        call, output = self.run_check(plan=self.PLAN, changes={"tracker": [object()]})
        self.assertEqual(
            [c.args for c in call.call_args_list], [("makemigrations", "tracker"), ("migrate",)]
        )
        self.assertFalse(call.call_args_list[0].kwargs["interactive"])
        self.assertIn("Modèles modifiés sans migration (tracker)", output)

    def test_a_question_for_a_human_stops_the_generation(self):
        from tracker.management.commands.runserver import NeedsHuman

        call, output = self.run_check(
            plan=self.PLAN, changes=NeedsHuman("renommage possible de company.notes en remarks")
        )
        self.assertEqual([c.args[0] for c in call.call_args_list], ["migrate"])
        self.assertIn("renommage possible de company.notes en remarks", output)
        self.assertIn("makemigrations", output)

    def test_the_guard_refuses_what_django_would_ask_about(self):
        """A real change detector run on a doctored model state."""
        from django.apps import apps
        from django.db import models
        from django.db.migrations.autodetector import MigrationAutodetector
        from django.db.migrations.loader import MigrationLoader
        from django.db.migrations.state import ProjectState
        from django.utils import translation

        from tracker.management.commands.runserver import GuardedQuestioner, NeedsHuman

        loader = MigrationLoader(None, ignore_no_migrations=True)

        def detect(mutate):
            to_state = ProjectState.from_apps(apps)
            mutate(to_state.models["tracker", "company"])
            detector = MigrationAutodetector(loader.project_state(), to_state, GuardedQuestioner())
            with translation.override(None):
                return detector.changes(graph=loader.graph)

        def rename(model_state):
            model_state.fields["remarks"] = model_state.fields.pop("notes")

        def mandatory(model_state):
            model_state.fields["vat"] = models.CharField(max_length=20)

        def harmless(model_state):
            model_state.fields["vat"] = models.CharField(max_length=20, blank=True, default="")

        with self.assertRaises(NeedsHuman) as caught:
            detect(rename)
        self.assertIn("renommage possible de company.notes en remarks", str(caught.exception))
        with self.assertRaises(NeedsHuman) as caught:
            detect(mandatory)
        self.assertIn("company.vat est obligatoire", str(caught.exception))
        self.assertEqual(sorted(detect(harmless)), ["tracker"])

    def test_the_french_locale_does_not_fake_changes_in_contrib_apps(self):
        """Verbose names are compared with the English strings frozen in
        Django's migrations; the detector must run with translations off."""
        from django.utils import translation

        with translation.override("fr-be"):
            call, output = self.run_check(plan=[])
        call.assert_not_called()
        self.assertEqual(output, "")


class OwnershipMigrationTests(TransactionTestCase):
    """The ownership migrations against rows recorded before accounts existed.

    A ``TransactionTestCase``: SQLite refuses schema changes inside the
    transaction a ``TestCase`` wraps around each test.
    """

    BEFORE = [("tracker", "0001_initial")]

    def rewind(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.BEFORE)
        return executor.loader.project_state(self.BEFORE).apps

    def replay(self):
        call_command("migrate", verbosity=0)

    def tearDown(self):
        self.replay()

    def seed(self, apps):
        Company = apps.get_model("tracker", "Company")
        Application = apps.get_model("tracker", "Application")
        Document = apps.get_model("tracker", "Document")
        Platform = apps.get_model("tracker", "Platform")
        SkillGap = apps.get_model("tracker", "SkillGap")
        company = Company.objects.create(name="Acme", slug="acme")
        Application.objects.create(company=company, title="Poste", slug="acme-poste")
        Document.objects.create(kind="cv", label="CV", file="documents/bibliotheque/cv.docx")
        Platform.objects.create(name="LinkedIn")
        SkillGap.objects.create(name="Terraform")

    def assert_everything_belongs_to(self, user):
        for model in (Application, Company, Document, Platform, SkillGap):
            with self.subTest(model=model.__name__):
                self.assertEqual(model.objects.count(), 1)
                self.assertEqual(model.objects.get().owner, user)

    def test_orphans_are_parked_on_a_local_account_awaiting_onboarding(self):
        apps = self.rewind()
        self.seed(apps)
        self.replay()

        user = get_user_model().objects.get()
        self.assertEqual(user.username, LOCAL_USERNAME)
        self.assertFalse(user.has_usable_password())
        self.assertFalse(profile_for(user).is_onboarded)
        self.assertTrue(preferences_for(user).pk)
        self.assert_everything_belongs_to(user)

    def test_orphans_go_to_the_only_existing_account(self):
        apps = self.rewind()
        # ``auth`` is untouched by the rewind: the live model is the right one.
        admin = get_user_model().objects.create_user("admin", password="x")
        self.seed(apps)
        self.replay()

        User = get_user_model()
        self.assertEqual(User.objects.count(), 1)
        self.assert_everything_belongs_to(User.objects.get(pk=admin.pk))
        self.assertFalse(profile_for(User.objects.get()).is_onboarded)

    def test_an_empty_database_gets_no_account(self):
        self.rewind()
        self.replay()
        self.assertEqual(get_user_model().objects.count(), 0)


# ---------------------------------------------------------------------------
# The hexagon: ports, adapters, rules and use cases
# ---------------------------------------------------------------------------


@override_settings(MEDIA_ROOT=MEDIA)
class PersistenceContractTests(TestCase):
    """Every port behaves the same on the ORM adapter and on the in-memory
    one — owner scoping, ``NotFound``, ``None`` last in every sort."""

    ON = dt.date(2026, 9, 2)

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.lionel = make_user("Lionel", username="lionel")
        cls.marie = make_user("Marie", username="marie")

    def each_adapter(self, check):
        for name, store in (("django_orm", DjangoPersistence()), ("memory", MemoryPersistence())):
            with self.subTest(adapter=name):
                check(store)

    def application(self, store, owner, company_name, **fields):
        company = store.companies.get_or_create(owner, company_name, Sector.PRIVATE)
        defaults = {"title": "Poste", "status": Status.TO_APPLY, "score": 72}
        defaults.update(fields)
        return store.applications.add(Application(owner=owner, company=company, **defaults))

    def upload(self, store, application, name, kind=DocumentKind.CV, primary=False, **fields):
        return store.documents.add(
            Document(
                application=application, kind=kind, label=name, is_primary=primary,
                file=SimpleUploadedFile(name, b"x"), **fields,
            )
        )

    def test_get_is_scoped_on_the_owner(self):
        def check(store):
            mine = self.application(store, self.lionel, "Alpha")
            self.assertEqual(store.applications.get(self.lionel, mine.pk).pk, mine.pk)
            self.assertEqual(store.applications.detail(self.lionel, mine.pk).pk, mine.pk)
            for lookup in (store.applications.get, store.applications.detail):
                with self.assertRaises(NotFound):
                    lookup(self.marie, mine.pk)
                with self.assertRaises(NotFound):
                    lookup(self.lionel, mine.pk + 1000)

            loaded = store.applications.get(self.lionel, mine.pk)
            with self.assertNumQueries(0):
                self.assertEqual(loaded.company.name, "Alpha")
                self.assertIsNone(loaded.source_platform)
            if isinstance(store, DjangoPersistence):
                with self.assertNumQueries(0):
                    self.assertIsNotNone(loaded.owner.preferences)

            event = store.events.add(mine, EventKind.NOTE, "Note")
            self.assertEqual(store.events.get(self.lionel, event.pk).pk, event.pk)
            with self.assertRaises(NotFound):
                store.events.get(self.marie, event.pk)

            contact = store.contacts.add(Contact(application=mine, name="Marie Dupont"))
            self.assertEqual(store.contacts.get(self.lionel, contact.pk).pk, contact.pk)
            with self.assertRaises(NotFound):
                store.contacts.get(self.marie, contact.pk)

            document = self.upload(store, mine, "CV.docx")
            self.assertEqual(store.documents.get(self.lionel, document.pk).pk, document.pk)
            with self.assertRaises(NotFound):
                store.documents.get(self.marie, document.pk)

            gap = SkillGap(owner=self.lionel, name="Terraform")
            store.skill_gaps.save(gap)
            self.assertEqual(store.skill_gaps.get(self.lionel, gap.pk).pk, gap.pk)
            with self.assertRaises(NotFound):
                store.skill_gaps.get(self.marie, gap.pk)

        self.each_adapter(check)

    def test_add_assigns_pk_slug_and_timestamps(self):
        def check(store):
            first = self.application(store, self.lionel, "Alpha", title="R&D Engineer")
            self.assertIsNotNone(first.pk)
            self.assertEqual(first.slug, "alpha-rd-engineer")
            self.assertIsNotNone(first.created_at)
            self.assertIsNotNone(first.updated_at)
            second = self.application(store, self.lionel, "Alpha", title="R&D Engineer")
            self.assertEqual(second.slug, "alpha-rd-engineer-2")

        self.each_adapter(check)

    def test_save_with_fields_persists_them_and_moves_updated_at(self):
        def check(store):
            application = self.application(store, self.lionel, "Alpha")
            mark = timezone.now() - dt.timedelta(days=1)
            application.updated_at = mark
            application.follow_up_on = dt.date(2026, 10, 1)
            application.status = Status.SENT  # touched but not listed: must not be written
            store.applications.save(application, fields=["follow_up_on"])
            reloaded = store.applications.get(self.lionel, application.pk)
            self.assertEqual(reloaded.follow_up_on, dt.date(2026, 10, 1))
            self.assertEqual(reloaded.status, Status.TO_APPLY)
            self.assertGreater(reloaded.updated_at, mark)

        self.each_adapter(check)

    def test_remove_takes_the_satellites_along(self):
        def check(store):
            application = self.application(store, self.lionel, "Alpha")
            event = store.events.add(application, EventKind.NOTE, "Note")
            contact = store.contacts.add(Contact(application=application, name="X"))
            document = self.upload(store, application, "CV.docx")
            pks = (application.pk, event.pk, contact.pk, document.pk)
            store.applications.remove(application)
            with self.assertRaises(NotFound):
                store.applications.get(self.lionel, pks[0])
            with self.assertRaises(NotFound):
                store.events.get(self.lionel, pks[1])
            with self.assertRaises(NotFound):
                store.contacts.get(self.lionel, pks[2])
            with self.assertRaises(NotFound):
                store.documents.get(self.lionel, pks[3])
            self.assertEqual(store.documents.count(self.lionel), 0)

        self.each_adapter(check)

    def test_by_status_orders_by_score_then_name_with_unscored_last(self):
        def check(store):
            gamma = self.application(store, self.lionel, "Gamma", score=90)
            beta = self.application(store, self.lionel, "Beta", score=None)
            alpha = self.application(store, self.lionel, "Alpha", score=90)
            delta = self.application(store, self.lionel, "Delta", score=50, status=Status.SENT)
            self.application(store, self.marie, "Alpha", score=99)
            rows = store.applications.by_status(self.lionel, [Status.TO_APPLY])
            self.assertEqual([row.pk for row in rows], [alpha.pk, gamma.pk, beta.pk])
            rows = store.applications.by_status(self.lionel, [Status.TO_APPLY, Status.SENT])
            self.assertEqual([row.pk for row in rows], [alpha.pk, gamma.pk, delta.pk, beta.pk])

        self.each_adapter(check)

    def test_status_counts(self):
        def check(store):
            self.assertEqual(store.applications.status_counts(self.lionel), {})
            self.application(store, self.lionel, "Alpha")
            self.application(store, self.lionel, "Beta")
            self.application(store, self.lionel, "Gamma", status=Status.SENT)
            self.application(store, self.marie, "Alpha", status=Status.SENT)
            self.assertEqual(
                store.applications.status_counts(self.lionel),
                {Status.TO_APPLY: 2, Status.SENT: 1},
            )

        self.each_adapter(check)

    def test_needing_attention_matches_due_follow_ups_and_stale_rows(self):
        on = self.ON

        def check(store):
            due = self.application(
                store, self.lionel, "Alpha", status=Status.SENT,
                applied_on=on - dt.timedelta(days=3), follow_up_on=on - dt.timedelta(days=1),
            )
            stale = self.application(
                store, self.lionel, "Beta", status=Status.SENT,
                applied_on=on - dt.timedelta(days=20),
            )
            interview_due = self.application(
                store, self.lionel, "Gamma", status=Status.INTERVIEW,
                applied_on=on - dt.timedelta(days=10), follow_up_on=on,
            )
            # Fresh, reminder later; not in flight; closed; someone else's.
            self.application(
                store, self.lionel, "Delta", status=Status.SENT,
                applied_on=on - dt.timedelta(days=3), follow_up_on=on + dt.timedelta(days=5),
            )
            self.application(
                store, self.lionel, "Epsilon", status=Status.TO_APPLY,
                follow_up_on=on - dt.timedelta(days=1),
            )
            self.application(
                store, self.lionel, "Zeta", status=Status.REJECTED,
                applied_on=on - dt.timedelta(days=30), closed_on=on,
            )
            self.application(
                store, self.marie, "Alpha", status=Status.SENT,
                applied_on=on - dt.timedelta(days=20),
            )
            rows = store.applications.needing_attention(self.lionel, stale_days=14, on=on)
            self.assertEqual([row.pk for row in rows], [due.pk, interview_due.pk, stale.pk])
            self.assertEqual(
                store.applications.attention_count(self.lionel, stale_days=14, on=on), 3
            )
            self.assertEqual(
                store.applications.attention_count(self.lionel, stale_days=30, on=on), 2
            )

        self.each_adapter(check)

    def test_scores_skips_unscored_and_written_off_offers(self):
        def check(store):
            self.application(store, self.lionel, "Alpha", score=80)
            self.application(store, self.lionel, "Beta", score=None)
            self.application(store, self.lionel, "Gamma", score=60, status=Status.BACKLOG)
            self.application(store, self.lionel, "Delta", score=70, status=Status.SENT)
            self.application(store, self.lionel, "Epsilon", score=99, status=Status.DISCARDED)
            self.application(store, self.marie, "Alpha", score=10)
            scores = store.applications.scores(self.lionel, [Status.TO_APPLY, Status.BACKLOG])
            self.assertEqual(sorted(scores), [60, 80])
            self.assertEqual(store.applications.scores(self.lionel, [Status.DISCARDED]), [])

        self.each_adapter(check)

    def test_companies_are_found_case_insensitively_one_list_per_owner(self):
        def check(store):
            created = store.companies.get_or_create(self.lionel, "Alpha", Sector.PRIVATE)
            self.assertIsNotNone(created.pk)
            self.assertEqual(created.slug, "alpha")
            same = store.companies.get_or_create(self.lionel, "  alpha ", Sector.PUBLIC)
            self.assertEqual(same.pk, created.pk)
            self.assertEqual(same.sector, Sector.PUBLIC)
            untouched = store.companies.get_or_create(self.lionel, "ALPHA")
            self.assertEqual(untouched.pk, created.pk)
            self.assertEqual(untouched.sector, Sector.PUBLIC)
            theirs = store.companies.get_or_create(self.marie, "alpha")
            self.assertNotEqual(theirs.pk, created.pk)
            self.assertEqual(theirs.owner_id, self.marie.pk)
            self.assertEqual(store.companies.get_or_create(self.lionel, "Beta").sector, Sector.UNKNOWN)

        self.each_adapter(check)

    def test_documents_inherit_the_owner_demote_by_kind_and_count(self):
        def check(store):
            application = self.application(store, self.lionel, "Alpha")
            first = self.upload(store, application, "CV1.docx", primary=True)
            second = self.upload(store, application, "CV2.docx", primary=True)
            letter = self.upload(
                store, application, "LM.docx", kind=DocumentKind.COVER_LETTER, primary=True
            )
            self.assertEqual(first.owner_id, self.lionel.pk)

            store.documents.demote_primary(application, DocumentKind.CV)
            self.assertFalse(store.documents.get(self.lionel, first.pk).is_primary)
            self.assertFalse(store.documents.get(self.lionel, second.pk).is_primary)
            self.assertTrue(store.documents.get(self.lionel, letter.pk).is_primary)

            library = store.documents.add(
                Document(owner=self.lionel, kind=DocumentKind.CV, label="Base",
                         file=SimpleUploadedFile("base.docx", b"x"))
            )
            self.assertIsNone(library.application_id)
            self.assertEqual(store.documents.count(self.lionel), 4)
            self.assertEqual(store.documents.count(self.marie), 0)
            with self.assertRaises(ValueError):
                store.documents.add(
                    Document(owner=self.marie, application=application, kind=DocumentKind.CV,
                             label="Intrus", file=SimpleUploadedFile("x.docx", b"x"))
                )
            pk = library.pk
            store.documents.remove(library)
            with self.assertRaises(NotFound):
                store.documents.get(self.lionel, pk)
            self.assertEqual(store.documents.count(self.lionel), 3)

        self.each_adapter(check)

    def test_contacts_and_events_are_removed_in_place(self):
        def check(store):
            application = self.application(store, self.lionel, "Alpha")
            contact = store.contacts.add(Contact(application=application, name="Marie Dupont"))
            event = store.events.add(
                application, EventKind.CALL, "Appel", "20 minutes.", dt.date(2026, 9, 1)
            )
            self.assertEqual(event.happened_on, dt.date(2026, 9, 1))
            self.assertEqual(event.detail, "20 minutes.")
            self.assertEqual(store.events.add(application, EventKind.NOTE, "x").happened_on,
                             timezone.localdate())
            contact_pk, event_pk = contact.pk, event.pk
            store.contacts.remove(contact)
            store.events.remove(event)
            with self.assertRaises(NotFound):
                store.contacts.get(self.lionel, contact_pk)
            with self.assertRaises(NotFound):
                store.events.get(self.lionel, event_pk)

        self.each_adapter(check)

    def test_skill_gaps_save_only_the_given_fields(self):
        def check(store):
            gap = SkillGap(owner=self.lionel, name="Terraform")
            store.skill_gaps.save(gap)
            self.assertIsNotNone(gap.pk)
            gap.status = GapStatus.DOING
            gap.demand_count = 99  # touched but not listed: must not be written
            store.skill_gaps.save(gap, fields=["status"])
            reloaded = store.skill_gaps.get(self.lionel, gap.pk)
            self.assertEqual(reloaded.status, GapStatus.DOING)
            self.assertEqual(reloaded.demand_count, 0)

        self.each_adapter(check)

    def test_companies_with_case_variants_resolve_to_the_first_one(self):
        """The uniqueness constraint is on the exact name: "Acme" and "ACME"
        can coexist (admin, import) and must not make the lookup raise."""

        def check(store):
            if isinstance(store, DjangoPersistence):
                first = Company.objects.create(owner=self.lionel, name="Acme")
                Company.objects.create(owner=self.lionel, name="ACME")
            else:
                table = store.companies._table
                first = table.insert(Company(owner=self.lionel, name="Acme", slug="acme"))
                table.insert(Company(owner=self.lionel, name="ACME", slug="acme-2"))
            self.assertEqual(store.companies.get_or_create(self.lionel, "acme").pk, first.pk)

        self.each_adapter(check)

    def test_an_application_cannot_borrow_another_owner_s_company(self):
        def check(store):
            theirs = store.companies.get_or_create(self.marie, "Alpha")
            with self.assertRaises(ValueError):
                store.applications.add(Application(owner=self.lionel, company=theirs, title="Poste"))

        self.each_adapter(check)


class DomainRuleTests(SimpleTestCase):
    """The rules on bare instances: no database, no adapter."""

    TODAY = dt.date(2026, 9, 2)

    def test_plan_transition_reports_what_it_touched(self):
        application = Application(status=Status.TO_APPLY)
        transition = domain.plan_transition(
            application, Status.SENT, today=self.TODAY, follow_up_days=10
        )
        self.assertEqual(transition.fields, ("status", "applied_on", "follow_up_on", "closed_on"))
        self.assertEqual(transition.event_kind, EventKind.APPLIED)
        self.assertEqual(transition.event_title, "À postuler → Candidature envoyée")
        self.assertEqual(application.applied_on, self.TODAY)
        self.assertEqual(application.follow_up_on, self.TODAY + dt.timedelta(days=10))
        self.assertIsNone(application.closed_on)

        closing = domain.plan_transition(
            application, Status.REJECTED, today=self.TODAY, follow_up_days=10
        )
        self.assertEqual(closing.fields, ("status", "closed_on", "follow_up_on"))
        self.assertEqual(closing.event_kind, EventKind.REJECTION)
        self.assertIsNone(application.follow_up_on)
        self.assertEqual(application.closed_on, self.TODAY)

    def test_the_follow_up_delay_is_only_resolved_when_a_reminder_is_needed(self):
        never = mock.Mock(side_effect=AssertionError("résolu pour rien"))
        application = Application(
            status=Status.SENT, applied_on=self.TODAY, follow_up_on=self.TODAY
        )
        self.assertIsNotNone(
            domain.plan_transition(
                application, Status.INTERVIEW, today=self.TODAY, follow_up_days=never
            )
        )
        never.assert_not_called()
        resolved = mock.Mock(return_value=3)
        application = Application(status=Status.TO_APPLY)
        domain.plan_transition(application, Status.SENT, today=self.TODAY, follow_up_days=resolved)
        self.assertEqual(application.follow_up_on, self.TODAY + dt.timedelta(days=3))

    def test_a_same_or_unknown_status_is_not_a_transition(self):
        application = Application(status=Status.TO_APPLY)
        for value in (Status.TO_APPLY, "nope", ""):
            with self.subTest(value=value):
                self.assertIsNone(
                    domain.plan_transition(application, value, today=self.TODAY, follow_up_days=10)
                )
        self.assertEqual(application.status, Status.TO_APPLY)

    def test_stale_and_attention_predicates(self):
        sent = Application(status=Status.SENT, applied_on=self.TODAY - dt.timedelta(days=14))
        self.assertTrue(domain.is_stale(sent, stale_days=14, today=self.TODAY))
        self.assertFalse(domain.is_stale(sent, stale_days=15, today=self.TODAY))
        never = mock.Mock(side_effect=AssertionError("résolu pour rien"))
        self.assertFalse(
            domain.is_stale(Application(status=Status.TO_APPLY), stale_days=never, today=self.TODAY)
        )
        due = Application(status=Status.INTERVIEW, follow_up_on=self.TODAY)
        self.assertTrue(domain.needs_attention(due, stale_days=never, today=self.TODAY))
        later = Application(status=Status.INTERVIEW, follow_up_on=self.TODAY + dt.timedelta(days=1))
        self.assertFalse(domain.needs_attention(later, stale_days=14, today=self.TODAY))
        parked = Application(status=Status.TO_APPLY, follow_up_on=self.TODAY)
        self.assertFalse(domain.needs_attention(parked, stale_days=14, today=self.TODAY))


class ServiceRuleTests(SimpleTestCase):
    """The use cases on the in-memory adapter: no database, no files."""

    TODAY = dt.date(2026, 9, 2)

    def setUp(self):
        User = get_user_model()
        self.store = MemoryPersistence()
        self.user = User(pk=1, username="lionel")
        self.other = User(pk=2, username="marie")
        self.company = Company(pk=1, owner=self.user, name="Acme")

    def application(self, **fields):
        defaults = {
            "owner": self.user, "company": self.company, "title": "Poste",
            "status": Status.TO_APPLY, "score": 72,
        }
        defaults.update(fields)
        return self.store.applications.add(Application(**defaults))

    def change(self, application, status, **kwargs):
        kwargs.setdefault("follow_up_days", 10)
        return services.change_status(
            application, status, persistence=self.store, today=self.TODAY, **kwargs
        )

    def events(self, application):
        return [e for e in self.store.events.rows() if e.application_id == application.pk]

    def test_moving_to_sent_stamps_the_date_and_schedules_a_follow_up(self):
        application = self.application()
        self.assertTrue(self.change(application, Status.SENT, note="Via LinkedIn"))
        self.assertEqual(application.applied_on, self.TODAY)
        self.assertEqual(application.follow_up_on, self.TODAY + dt.timedelta(days=10))
        (event,) = self.events(application)
        self.assertEqual(event.kind, EventKind.APPLIED)
        self.assertEqual(event.title, "À postuler → Candidature envoyée")
        self.assertEqual(event.detail, "Via LinkedIn")
        self.assertEqual(event.happened_on, self.TODAY)

    def test_the_follow_up_delay_is_the_caller_s(self):
        application = self.application()
        self.change(application, Status.SENT, follow_up_days=3)
        self.assertEqual(application.follow_up_on, self.TODAY + dt.timedelta(days=3))

    def test_closing_clears_the_follow_up_and_stamps_the_close_date(self):
        application = self.application()
        self.change(application, Status.SENT)
        self.change(application, Status.REJECTED)
        self.assertIsNone(application.follow_up_on)
        self.assertEqual(application.closed_on, self.TODAY)
        self.assertEqual([e.kind for e in self.events(application)],
                         [EventKind.APPLIED, EventKind.REJECTION])

    def test_reopening_a_closed_application_clears_the_close_date(self):
        application = self.application()
        self.change(application, Status.REJECTED)
        self.change(application, Status.SENT)
        self.assertIsNone(application.closed_on)
        self.assertEqual(application.applied_on, self.TODAY)

    def test_a_no_op_or_unknown_transition_logs_nothing(self):
        application = self.application()
        self.assertFalse(self.change(application, Status.TO_APPLY))
        self.assertFalse(self.change(application, "nope"))
        self.assertEqual(self.events(application), [])

    def test_an_existing_applied_date_is_preserved(self):
        earlier = self.TODAY - dt.timedelta(days=12)
        application = self.application(applied_on=earlier)
        self.change(application, Status.SENT)
        self.assertEqual(application.applied_on, earlier)

    def test_advance_walks_the_pipeline_and_stops_at_the_end(self):
        application = self.application(status=Status.BACKLOG)
        walked = []
        while (
            nxt := services.advance(
                application, follow_up_days=10, persistence=self.store, today=self.TODAY
            )
        ) is not None:
            walked.append(nxt)
        self.assertEqual(
            walked,
            [Status.TO_APPLY, Status.SENT, Status.SCREENING, Status.INTERVIEW,
             Status.TECHNICAL, Status.OFFER, Status.ACCEPTED],
        )
        self.assertEqual(application.status, Status.ACCEPTED)
        self.assertEqual(application.applied_on, self.TODAY)
        self.assertEqual(application.closed_on, self.TODAY)
        self.assertIsNone(application.follow_up_on)
        self.assertEqual(len(self.events(application)), 7)

    def test_schedule_and_mark_follow_up(self):
        application = self.application(status=Status.SENT, applied_on=self.TODAY)
        services.schedule_follow_up(application, dt.date(2026, 9, 20), persistence=self.store)
        self.assertEqual(application.follow_up_on, dt.date(2026, 9, 20))

        next_on = services.mark_followed_up(
            application, follow_up_days=4, persistence=self.store, today=self.TODAY
        )
        self.assertEqual(next_on, self.TODAY + dt.timedelta(days=4))
        self.assertEqual(application.follow_up_on, next_on)
        (event,) = self.events(application)
        self.assertEqual((event.kind, event.title, event.happened_on),
                         (EventKind.FOLLOW_UP, "Relance envoyée", self.TODAY))

        services.schedule_follow_up(application, None, persistence=self.store)
        self.assertIsNone(application.follow_up_on)

    def test_record_and_add_event(self):
        application = self.application()
        created = services.record_application(application, persistence=self.store)
        self.assertEqual((created.kind, created.title), (EventKind.NOTE, "Candidature créée"))
        typed = ActivityEvent(
            kind=EventKind.CALL, title="Appel", detail="20 min", happened_on=dt.date(2026, 9, 1)
        )
        stored = services.add_event(application, typed, persistence=self.store)
        self.assertEqual(stored.application_id, application.pk)
        self.assertEqual((stored.kind, stored.title, stored.detail, stored.happened_on),
                         (EventKind.CALL, "Appel", "20 min", dt.date(2026, 9, 1)))
        self.assertEqual(len(self.events(application)), 2)

    def test_attach_document_demotes_the_previous_primary_and_logs(self):
        application = self.application()

        def upload(name):
            return services.attach_document(
                self.user, application,
                Document(kind=DocumentKind.CV, is_primary=True, file=SimpleUploadedFile(name, b"x")),
                label=name, persistence=self.store,
            )

        first = upload("CV1.docx")
        second = upload("CV2.docx")
        self.assertFalse(first.is_primary)
        self.assertTrue(second.is_primary)
        self.assertEqual(first.owner_id, self.user.pk)
        self.assertIs(first.application, application)
        self.assertEqual(
            [e.title for e in self.events(application)],
            ["Document ajouté : CV1.docx", "Document ajouté : CV2.docx"],
        )

        library = services.attach_document(
            self.user, None,
            Document(kind=DocumentKind.CV, file=SimpleUploadedFile("base.docx", b"x")),
            label="Base", persistence=self.store,
        )
        self.assertIsNone(library.application)
        self.assertEqual(library.owner_id, self.user.pk)
        self.assertEqual(len(self.events(application)), 2)
        self.assertEqual(self.store.documents.count(self.user), 3)

    def test_deletions_return_the_application_and_refuse_a_stranger(self):
        application = self.application()
        event = self.store.events.add(application, EventKind.NOTE, "x")
        contact = self.store.contacts.add(Contact(application=application, name="X"))
        document = services.attach_document(
            self.user, application,
            Document(kind=DocumentKind.CV, file=SimpleUploadedFile("cv.docx", b"x")),
            label="CV", persistence=self.store,
        )
        for remove, pk in (
            (services.delete_event, event.pk),
            (services.delete_contact, contact.pk),
            (services.delete_document, document.pk),
        ):
            with self.subTest(remove=remove.__name__):
                with self.assertRaises(NotFound):
                    remove(self.other, pk, persistence=self.store)
        self.assertIs(services.delete_event(self.user, event.pk, persistence=self.store), application)
        self.assertIs(
            services.delete_contact(self.user, contact.pk, persistence=self.store), application
        )
        removed = services.delete_document(self.user, document.pk, persistence=self.store)
        self.assertIs(removed.application, application)
        self.assertEqual(removed.label, "CV")
        self.assertEqual(self.store.documents.count(self.user), 0)
        self.assertIs(
            services.delete_application(self.user, application.pk, persistence=self.store),
            application,
        )
        with self.assertRaises(NotFound):
            self.store.applications.get(self.user, application.pk)

    def test_set_gap_status(self):
        gap = SkillGap(owner=self.user, name="Terraform")
        self.store.skill_gaps.save(gap)
        self.assertEqual(
            services.set_gap_status(self.user, gap.pk, GapStatus.DONE, persistence=self.store).status,
            GapStatus.DONE,
        )
        with self.assertRaises(ValueError):
            services.set_gap_status(self.user, gap.pk, "bogus", persistence=self.store)
        with self.assertRaises(NotFound):
            services.set_gap_status(self.other, gap.pk, GapStatus.DONE, persistence=self.store)

    def populate(self):
        self.application(score=80)
        self.application(status=Status.BACKLOG, score=None)
        self.application(status=Status.SENT, score=60, applied_on=self.TODAY - dt.timedelta(days=20))
        self.application(
            status=Status.INTERVIEW, score=90,
            applied_on=self.TODAY - dt.timedelta(days=5), follow_up_on=self.TODAY,
        )
        self.application(status=Status.REJECTED, score=30, closed_on=self.TODAY)
        self.application(status=Status.DISCARDED, score=99)
        self.store.applications.add(
            Application(owner=self.other, company=Company(pk=2, owner=self.other, name="Autre"),
                        title="Pas à moi", status=Status.TO_APPLY, score=100)
        )

    def test_dashboard_stats(self):
        self.populate()
        stats = services.dashboard_stats(self.user, persistence=self.store, today=self.TODAY)
        self.assertEqual(
            stats,
            {
                "tracked": 5, "to_apply": 1, "backlog": 1, "in_flight": 2, "interviewing": 1,
                "offers": 0, "rejected": 1, "discarded": 1, "sent_total": 3, "answered": 2,
                "response_rate": 67, "average_score": 77, "today": self.TODAY,
            },
        )
        empty = services.dashboard_stats(self.other, persistence=MemoryPersistence())
        self.assertEqual((empty["tracked"], empty["response_rate"], empty["average_score"]),
                         (0, None, None))

    def test_board(self):
        to_apply = self.application(score=80)
        backlog = self.application(status=Status.BACKLOG, score=None)
        sent = self.application(status=Status.SENT, score=60)
        self.application(status=Status.REJECTED)
        board = services.board(self.user, persistence=self.store)
        self.assertEqual([column["status"] for column in board], PIPELINE_STATUSES)
        columns = {column["status"]: column for column in board}
        self.assertEqual(columns[Status.TO_APPLY]["applications"], [to_apply])
        self.assertEqual(columns[Status.BACKLOG]["applications"], [backlog])
        self.assertEqual(columns[Status.SENT]["applications"], [sent])
        self.assertEqual(columns[Status.OFFER]["applications"], [])
        self.assertEqual((columns[Status.TO_APPLY]["count"], columns[Status.OFFER]["count"]), (1, 0))
        self.assertEqual((columns[Status.TO_APPLY]["label"], columns[Status.TO_APPLY]["tone"]),
                         ("À postuler", "amber"))

    def test_nav_counters(self):
        self.populate()
        services.attach_document(
            self.user, None, Document(kind=DocumentKind.CV, file=SimpleUploadedFile("b.docx", b"x")),
            label="Base", persistence=self.store,
        )
        self.assertEqual(
            services.nav_counters(self.user, stale_days=14, today=self.TODAY, persistence=self.store),
            {"open": 4, "tracked": 5, "attention": 2, "documents": 1},
        )
        self.assertEqual(
            services.nav_counters(self.user, stale_days=30, today=self.TODAY, persistence=self.store)["attention"],
            1,
        )

    def test_attention(self):
        self.populate()
        rows = services.attention(self.user, stale_days=14, today=self.TODAY, persistence=self.store)
        self.assertEqual([row.status for row in rows], [Status.INTERVIEW, Status.SENT])


@override_settings(MEDIA_ROOT=MEDIA)
class TransactionTests(TestCase):
    def test_a_failed_timeline_entry_rolls_the_status_change_back(self):
        application = make_application()
        store = DjangoPersistence()
        with mock.patch.object(store.events, "add", side_effect=RuntimeError("panne")):
            with self.assertRaises(RuntimeError):
                services.change_status(
                    application, Status.SENT, follow_up_days=10, persistence=store
                )
        application.refresh_from_db()
        self.assertEqual(application.status, Status.TO_APPLY)
        self.assertIsNone(application.applied_on)
        self.assertIsNone(application.follow_up_on)
        self.assertEqual(application.events.count(), 0)


class PersistenceResolverTests(SimpleTestCase):
    def test_the_setting_names_the_adapter(self):
        self.assertIsInstance(persistence(), DjangoPersistence)
        with override_settings(PERSISTENCE_ADAPTER="tracker.adapters.memory.MemoryPersistence"):
            self.assertIsInstance(persistence(), MemoryPersistence)
            self.assertIs(persistence(), persistence())
        self.assertIsInstance(persistence(), DjangoPersistence)


@override_settings(MEDIA_ROOT=MEDIA)
class NullOrderingTests(TestCase):
    """Nullable sort keys go last on every engine — the rule that keeps
    SQLite and PostgreSQL agreeing on what the pages show."""

    def setUp(self):
        self.user = make_user("Lionel")
        today = timezone.localdate()

        def application(name, **fields):
            fields.setdefault("distance_km", None)
            return make_application(owner=self.user, company_name=name, **fields)

        self.alpha = application("Alpha", score=90)
        self.beta = application("Beta", score=None, distance_km=10)
        self.gamma = application("Gamma", score=90, distance_km=5)
        self.delta = application(
            "Delta", status=Status.SENT, score=70,
            applied_on=today - dt.timedelta(days=5), follow_up_on=today + dt.timedelta(days=3),
        )
        self.epsilon = application("Epsilon", status=Status.SENT, score=60)
        self.zeta = application("Zeta", status=Status.REJECTED, score=50, closed_on=today)
        self.eta = application("Eta", status=Status.REJECTED, score=40)

    def test_the_table_sorts(self):
        rows = queries.filtered_applications(self.user, {"sort": "-score"})
        self.assertEqual(rows, [self.alpha, self.gamma, self.delta, self.epsilon, self.beta])

        rows = queries.filtered_applications(self.user, {"sort": "pipeline"})
        self.assertEqual(rows, [self.alpha, self.gamma, self.beta, self.delta, self.epsilon])

        rows = queries.filtered_applications(self.user, {"sort": "-applied_on"})
        self.assertEqual(rows[0], self.delta)
        self.assertEqual(rows[-1], self.beta)

        rows = queries.filtered_applications(self.user, {"sort": "follow_up_on"})
        self.assertEqual(rows[0], self.delta)
        self.assertEqual(rows[-1], self.beta)

    def test_the_dashboard_lists(self):
        lists = queries.dashboard_lists(self.user, today=timezone.localdate())
        self.assertEqual(lists["to_apply"], [self.gamma, self.alpha, self.beta])
        self.assertEqual(lists["in_flight"], [self.delta, self.epsilon])
        self.assertEqual(lists["backlog"], [])

    def test_closed_files(self):
        self.assertEqual(queries.closed(self.user), [self.zeta, self.eta])


@override_settings(MEDIA_ROOT=MEDIA)
class QueryBudgetTests(OwnedTestCase):
    """Upper bounds calibrated on today's counts, so an N+1 shows up.

    Transaction bookkeeping (savepoints, which a ``TestCase`` turns every
    ``atomic()`` into) is not a data query and is left out of the count.
    """

    BOOKKEEPING = ("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")

    def setUp(self):
        super().setUp()
        self.application = make_application(company_name="Acme", status=Status.TO_APPLY)
        for index in range(3):
            make_document(
                application=self.application, kind=DocumentKind.CV, label=f"CV{index}.docx",
                language=Language.FR, is_primary=index == 0,
                file=SimpleUploadedFile(f"CV{index}.docx", b"x" * 10),
            )
        for name in ("Marie Dupont", "Jean Martin"):
            Contact.objects.create(application=self.application, name=name, role="Recruteur")
        today = timezone.localdate()
        for offset, kind in enumerate(
            [EventKind.NOTE, EventKind.CALL, EventKind.EMAIL, EventKind.INTERVIEW]
        ):
            ActivityEvent.objects.create(
                application=self.application, kind=kind, title=f"Événement {offset}",
                happened_on=today + dt.timedelta(days=offset - 2),
            )

    def count_queries(self, call):
        with CaptureQueriesContext(connection) as context:
            response = call()
        self.assertEqual(response.status_code, 200)
        return sum(
            1 for query in context.captured_queries
            if not query["sql"].startswith(self.BOOKKEEPING)
        )

    def test_application_detail(self):
        url = reverse("tracker:application_detail", args=[self.application.pk])
        self.assertLessEqual(self.count_queries(lambda: self.client.get(url)), 16)

    def test_dashboard(self):
        url = reverse("tracker:dashboard")
        self.assertLessEqual(self.count_queries(lambda: self.client.get(url)), 20)

    def test_set_status(self):
        url = reverse("tracker:set_status", args=[self.application.pk])
        post = lambda: self.client.post(  # noqa: E731
            url, {"status": Status.SENT, "source": "detail"}, HTTP_HX_REQUEST="true"
        )
        self.assertLessEqual(self.count_queries(post), 12)


class ArchitectureGuardTests(SimpleTestCase):
    """The layering, enforced by grep so it survives the next refactor."""

    ROOT = Path(tracker.__file__).parent

    def source(self, name: str) -> str:
        return (self.ROOT / name).read_text(encoding="utf-8")

    def test_views_and_services_never_query_the_orm_themselves(self):
        for name in ("views.py", "services.py"):
            text = self.source(name)
            for token in (".objects.", "owned_or_404(", "get_object_or_404(", ".for_user(",
                          "select_related(", "prefetch_related(", ".log(", "apply_status(",
                          "_default_manager"):
                with self.subTest(file=name, token=token):
                    self.assertNotIn(token, text)

    def test_rules_and_use_cases_never_take_the_model_shortcuts(self):
        for name in ("services.py", "domain.py", "adapters/memory.py"):
            text = self.source(name)
            for token in ("apply_status(", ".log(", "owner_preferences(", "preferences_for(",
                          ".objects."):
                with self.subTest(file=name, token=token):
                    self.assertNotIn(token, text)

    def test_services_stay_clear_of_django_plumbing(self):
        forbidden = {
            "services.py": ("django.db", "django.shortcuts", "jobhunt.plugins",
                            "tracker.queries", "tracker.adapters.django_orm"),
            "domain.py": ("django.db",),
            "adapters/memory.py": ("django.db",),
        }
        for name, modules in forbidden.items():
            text = self.source(name)
            for module in modules:
                with self.subTest(file=name, module=module):
                    self.assertNotRegex(
                        text, rf"^\s*(from|import)\s+{re.escape(module)}\b", re.MULTILINE
                    )


def tearDownModule():
    shutil.rmtree(MEDIA, ignore_errors=True)
