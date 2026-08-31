"""Views. Full pages render templates; HTMX endpoints return fragments."""

from __future__ import annotations

import datetime as dt
import json
from collections import OrderedDict

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods

from tracker.forms import (
    ApplicationFilterForm,
    ApplicationForm,
    ContactForm,
    DocumentForm,
    EventForm,
    FollowUpForm,
    NotesForm,
    QuickApplicationForm,
)
from tracker.models import (
    CLOSED_STATUSES,
    INTERVIEWING_STATUSES,
    IN_FLIGHT_STATUSES,
    OPEN_STATUSES,
    PIPELINE_STATUSES,
    STATUS_TONE,
    ActivityEvent,
    Application,
    Company,
    Contact,
    Document,
    EventKind,
    Language,
    Platform,
    Sector,
    SkillGap,
    Status,
    GapStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def toast(response: HttpResponse, message: str, tone: str = "success") -> HttpResponse:
    """Ask the client to pop a toast, via the HX-Trigger header."""
    payload = {"toast": {"message": message, "tone": tone}}
    existing = response.headers.get("HX-Trigger")
    if existing:
        merged = json.loads(existing)
        merged.update(payload)
        payload = merged
    response.headers["HX-Trigger"] = json.dumps(payload)
    return response


def company_names() -> list[str]:
    return list(Company.objects.order_by("name").values_list("name", flat=True))


def build_board(applications=None) -> list[dict]:
    """Group open applications into the pipeline columns."""
    queryset = applications if applications is not None else Application.objects.with_related()
    buckets: dict[str, list[Application]] = {status: [] for status in PIPELINE_STATUSES}
    for application in queryset.filter(status__in=PIPELINE_STATUSES).order_by(
        "-score", "company__name"
    ):
        buckets[application.status].append(application)
    return [
        {
            "status": status,
            "label": Status(status).label,
            "tone": STATUS_TONE.get(status, "slate"),
            "applications": buckets[status],
            "count": len(buckets[status]),
        }
        for status in PIPELINE_STATUSES
    ]


def dashboard_stats() -> dict:
    today = timezone.localdate()
    tracked = Application.objects.active()
    counts = {
        row["status"]: row["n"]
        for row in Application.objects.values("status").annotate(n=Count("id"))
    }

    def count_of(*statuses) -> int:
        return sum(counts.get(s, 0) for s in statuses)

    sent_or_beyond = count_of(*IN_FLIGHT_STATUSES, *[Status.ACCEPTED, Status.REJECTED, Status.GHOSTED])
    answered = count_of(*INTERVIEWING_STATUSES, Status.OFFER, Status.ACCEPTED, Status.REJECTED)
    scores = [a.score for a in tracked.filter(status__in=OPEN_STATUSES) if a.score is not None]

    return {
        "tracked": tracked.count(),
        "to_apply": count_of(Status.TO_APPLY),
        "backlog": count_of(Status.BACKLOG),
        "in_flight": count_of(*IN_FLIGHT_STATUSES),
        "interviewing": count_of(*INTERVIEWING_STATUSES),
        "offers": count_of(Status.OFFER, Status.ACCEPTED),
        "rejected": count_of(Status.REJECTED),
        "discarded": count_of(Status.DISCARDED),
        "sent_total": sent_or_beyond,
        "answered": answered,
        "response_rate": round(answered / sent_or_beyond * 100) if sent_or_beyond else None,
        "average_score": round(sum(scores) / len(scores)) if scores else None,
        "today": today,
    }


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def dashboard(request):
    today = timezone.localdate()
    base = Application.objects.with_related()

    attention = list(
        base.filter(
            Q(follow_up_on__lte=today, status__in=IN_FLIGHT_STATUSES)
            | Q(
                status=Status.SENT,
                applied_on__lte=today - dt.timedelta(days=settings.STALE_AFTER_DAYS),
            )
        ).order_by("follow_up_on", "applied_on")
    )

    to_apply = list(
        base.filter(status=Status.TO_APPLY).order_by("-score", "distance_km", "company__name")
    )
    in_flight = list(
        base.filter(status__in=IN_FLIGHT_STATUSES).order_by("applied_on", "-score")
    )
    backlog = list(base.filter(status=Status.BACKLOG).order_by("-score", "company__name"))

    upcoming = list(
        ActivityEvent.objects.select_related("application", "application__company")
        .filter(happened_on__gt=today)
        .order_by("happened_on")[:6]
    )
    recent = list(
        ActivityEvent.objects.select_related("application", "application__company")
        .filter(happened_on__lte=today)
        .exclude(application__status=Status.DISCARDED)
        .order_by("-happened_on", "-created_at")[:8]
    )

    stats = dashboard_stats()
    funnel_total = max(stats["tracked"], 1)
    funnel = [
        {"label": "Suivies", "value": stats["tracked"], "tone": "slate"},
        {"label": "À postuler", "value": stats["to_apply"], "tone": "amber"},
        {"label": "Envoyées", "value": stats["sent_total"], "tone": "blue"},
        {"label": "En entretien", "value": stats["interviewing"], "tone": "violet"},
        {"label": "Offres", "value": stats["offers"], "tone": "green"},
    ]
    for step in funnel:
        step["pct"] = round(step["value"] / funnel_total * 100)

    return render(
        request,
        "tracker/dashboard.html",
        {
            "page": "dashboard",
            "stats": stats,
            "funnel": funnel,
            "attention": attention,
            "to_apply": to_apply,
            "in_flight": in_flight,
            "backlog": backlog,
            "upcoming": upcoming,
            "recent": recent,
            "quick_form": QuickApplicationForm(),
            "company_names": company_names(),
            "stale_days": settings.STALE_AFTER_DAYS,
        },
    )


def pipeline(request):
    return render(
        request,
        "tracker/pipeline.html",
        {
            "page": "pipeline",
            "board": build_board(),
            "stats": dashboard_stats(),
            "closed": Application.objects.with_related()
            .filter(status__in=CLOSED_STATUSES)
            .exclude(status=Status.DISCARDED)
            .order_by("-closed_on"),
        },
    )


def _filtered_applications(request):
    form = ApplicationFilterForm(request.GET or None)
    queryset = Application.objects.with_related()
    data = form.cleaned_data if form.is_valid() else {}

    scope = data.get("scope") or "active"
    if scope == "active":
        queryset = queryset.filter(status__in=OPEN_STATUSES)
    elif scope == "closed":
        queryset = queryset.filter(status__in=CLOSED_STATUSES).exclude(status=Status.DISCARDED)
    elif scope == "discarded":
        queryset = queryset.filter(status=Status.DISCARDED)

    if statuses := data.get("status"):
        queryset = queryset.filter(status__in=statuses)
    if sector := data.get("sector"):
        queryset = queryset.filter(company__sector=sector)
    if language := data.get("language"):
        queryset = queryset.filter(cv_language=language)
    if work_mode := data.get("work_mode"):
        queryset = queryset.filter(work_mode=work_mode)
    if q := (data.get("q") or "").strip():
        queryset = queryset.filter(
            Q(company__name__icontains=q)
            | Q(title__icontains=q)
            | Q(location__icontains=q)
            | Q(summary__icontains=q)
            | Q(personal_notes__icontains=q)
            | Q(strengths__icontains=q)
            | Q(weaknesses__icontains=q)
            | Q(discard_reason__icontains=q)
        )

    sort = data.get("sort") or "-score"
    if sort == "pipeline":
        queryset = queryset.by_pipeline_order()
    elif sort == "-applied_on":
        queryset = queryset.order_by(models_f_desc("applied_on"), "-score")
    elif sort == "follow_up_on":
        queryset = queryset.order_by(models_f_asc("follow_up_on"), "-score")
    else:
        queryset = queryset.order_by(sort, "company__name")

    return form, queryset


def models_f_desc(field):
    from django.db.models import F

    return F(field).desc(nulls_last=True)


def models_f_asc(field):
    from django.db.models import F

    return F(field).asc(nulls_last=True)


def application_list(request):
    form, queryset = _filtered_applications(request)
    context = {
        "page": "list",
        "filter_form": form,
        "applications": queryset,
        "total": queryset.count(),
        "status_choices": Status.choices,
        "selected_statuses": form.cleaned_data.get("status", []) if form.is_valid() else [],
    }
    if request.headers.get("HX-Request"):
        return render(request, "tracker/partials/application_table.html", context)
    context["quick_form"] = QuickApplicationForm()
    context["company_names"] = company_names()
    return render(request, "tracker/application_list.html", context)


def application_detail(request, pk: int):
    application = get_object_or_404(
        Application.objects.with_related().prefetch_related("documents", "contacts", "events"),
        pk=pk,
    )
    return render(
        request,
        "tracker/application_detail.html",
        {
            "page": "list",
            "application": application,
            "notes_form": NotesForm(instance=application),
            "event_form": EventForm(),
            "document_form": DocumentForm(),
            "contact_form": ContactForm(),
            "follow_up_form": FollowUpForm(instance=application),
            "status_choices": Status.choices,
            "pipeline_statuses": PIPELINE_STATUSES,
            "stale_days": settings.STALE_AFTER_DAYS,
        },
    )


def application_create(request):
    if request.method == "POST":
        form = ApplicationForm(request.POST)
        if form.is_valid():
            application = form.save()
            application.log(EventKind.NOTE, "Candidature créée")
            messages.success(request, f"« {application.title} » ajoutée au suivi.")
            return redirect(application.get_absolute_url())
    else:
        form = ApplicationForm(initial={"status": Status.BACKLOG,
                                        "discovered_on": timezone.localdate()})
    return render(
        request,
        "tracker/application_form.html",
        {
            "page": "list",
            "form": form,
            "title": "Nouvelle candidature",
            "company_names": company_names(),
        },
    )


def application_update(request, pk: int):
    application = get_object_or_404(Application, pk=pk)
    previous_status = application.status
    if request.method == "POST":
        form = ApplicationForm(request.POST, instance=application)
        if form.is_valid():
            application = form.save()
            if application.status != previous_status:
                application.log(
                    EventKind.STATUS,
                    f"{Status(previous_status).label} → {Status(application.status).label}",
                )
            messages.success(request, "Candidature mise à jour.")
            return redirect(application.get_absolute_url())
    else:
        form = ApplicationForm(instance=application)
    return render(
        request,
        "tracker/application_form.html",
        {
            "page": "list",
            "form": form,
            "application": application,
            "title": f"Modifier — {application.company.name}",
            "company_names": company_names(),
        },
    )


@require_POST
def application_delete(request, pk: int):
    application = get_object_or_404(Application, pk=pk)
    label = str(application)
    application.delete()
    messages.success(request, f"« {label} » supprimée.")
    return redirect("tracker:application_list")


def document_library(request):
    library = Document.objects.filter(application__isnull=True).order_by("kind", "label")
    attached = (
        Document.objects.filter(application__isnull=False)
        .select_related("application", "application__company")
        .order_by("application__company__name", "kind")
    )
    grouped: OrderedDict[str, list[Document]] = OrderedDict()
    for document in attached:
        grouped.setdefault(document.application, []).append(document)
    return render(
        request,
        "tracker/documents.html",
        {
            "page": "documents",
            "library": library,
            "grouped": grouped,
            "library_form": DocumentForm(),
            "total": library.count() + attached.count(),
        },
    )


def insights(request):
    gaps = SkillGap.objects.all()
    platforms = Platform.objects.filter(is_lead=False)
    leads = Platform.objects.filter(is_lead=True)
    discarded = Application.objects.with_related().discarded().order_by("company__name")

    by_sector = [
        {
            "label": Sector(row["company__sector"]).label,
            "value": row["n"],
            "key": row["company__sector"],
        }
        for row in Application.objects.active()
        .values("company__sector")
        .annotate(n=Count("id"))
        .order_by("-n")
    ]
    by_language = [
        {"label": Language(row["cv_language"]).label, "value": row["n"]}
        for row in Application.objects.active()
        .values("cv_language")
        .annotate(n=Count("id"))
        .order_by("-n")
    ]
    scored = Application.objects.active().exclude(score__isnull=True)
    bands = [
        {"label": "80 % et plus", "value": scored.filter(score__gte=80).count(), "tone": "green"},
        {
            "label": "65 – 79 %",
            "value": scored.filter(score__gte=65, score__lt=80).count(),
            "tone": "amber",
        },
        {"label": "moins de 65 %", "value": scored.filter(score__lt=65).count(), "tone": "red"},
    ]
    # The trade-off the dossier actually optimises: match quality against commute.
    plotted = [
        a
        for a in Application.objects.with_related().active()
        if a.score is not None and a.distance_km is not None
    ]
    max_distance = max((a.distance_km for a in plotted), default=0)
    axis_max = max(40, ((max_distance // 10) + 1) * 10) if plotted else 40
    points = [
        {
            "application": a,
            # Formatted here, not in the template: under a French locale Django
            # renders 75.0 as "75,0", which is not a valid CSS length.
            "x": f"{a.distance_km / axis_max * 100:.2f}",
            "y": a.score,
            "tone": {"high": "green", "mid": "amber", "low": "red"}.get(a.score_band, "slate"),
        }
        for a in plotted
    ]

    max_sector = max((row["value"] for row in by_sector), default=1) or 1
    max_language = max((row["value"] for row in by_language), default=1) or 1
    max_band = max((row["value"] for row in bands), default=1) or 1
    max_gap = max((gap.demand_count for gap in gaps), default=1) or 1

    return render(
        request,
        "tracker/insights.html",
        {
            "page": "insights",
            "gaps": gaps,
            "platforms": platforms,
            "leads": leads,
            "discarded": discarded,
            "by_sector": by_sector,
            "by_language": by_language,
            "bands": bands,
            "max_sector": max_sector,
            "max_language": max_language,
            "max_band": max_band,
            "max_gap": max_gap,
            "points": points,
            "axis_max": axis_max,
            "stats": dashboard_stats(),
        },
    )


# ---------------------------------------------------------------------------
# HTMX endpoints
# ---------------------------------------------------------------------------


def _status_response(request, application: Application, source: str) -> HttpResponse:
    """Re-render whatever fragment the change was triggered from."""
    if source == "board":
        return render(request, "tracker/partials/board.html", {"board": build_board()})
    if source == "row":
        return render(
            request,
            "tracker/partials/application_row.html",
            {"application": application, "status_choices": Status.choices},
        )
    if source == "list":
        return render(
            request, "tracker/partials/listrow.html", {"application": application}
        )
    return render(
        request,
        "tracker/partials/status_panel.html",
        {
            "application": application,
            "status_choices": Status.choices,
            "follow_up_form": FollowUpForm(instance=application),
            "stale_days": settings.STALE_AFTER_DAYS,
        },
    )


@require_POST
def set_status(request, pk: int):
    application = get_object_or_404(Application.objects.with_related(), pk=pk)
    new_status = request.POST.get("status", "")
    if new_status not in Status.values:
        return HttpResponseBadRequest("Statut inconnu.")

    changed = application.apply_status(new_status, note=request.POST.get("note", ""))
    source = request.POST.get("source") or request.GET.get("source") or "detail"
    response = _status_response(request, application, source)
    response.headers["HX-Trigger-After-Settle"] = json.dumps({"stats-changed": True})
    if changed:
        toast(response, f"{application.company.name} → {Status(new_status).label}")
    return response


@require_POST
def advance_status(request, pk: int):
    application = get_object_or_404(Application.objects.with_related(), pk=pk)
    nxt = application.next_status
    source = request.POST.get("source", "board")
    if not nxt:
        response = _status_response(request, application, source)
        return toast(response, "Cette candidature est déjà au bout du parcours.", "info")
    application.apply_status(nxt)
    response = _status_response(request, application, source)
    response.headers["HX-Trigger-After-Settle"] = json.dumps({"stats-changed": True})
    return toast(response, f"{application.company.name} → {Status(nxt).label}")


@require_POST
def set_follow_up(request, pk: int):
    application = get_object_or_404(Application.objects.with_related(), pk=pk)
    raw = (request.POST.get("follow_up_on") or "").strip()
    preset = request.POST.get("in_days")

    if preset:
        try:
            application.follow_up_on = timezone.localdate() + dt.timedelta(days=int(preset))
        except ValueError:
            return HttpResponseBadRequest("Délai invalide.")
    elif raw:
        try:
            application.follow_up_on = dt.date.fromisoformat(raw)
        except ValueError:
            return HttpResponseBadRequest("Date invalide.")
    else:
        application.follow_up_on = None

    application.save(update_fields=["follow_up_on", "updated_at"])
    source = request.POST.get("source", "detail")
    response = _status_response(request, application, source)
    if application.follow_up_on:
        response = toast(
            response, f"Relance programmée le {application.follow_up_on:%d/%m/%Y}."
        )
    else:
        response = toast(response, "Relance retirée.", "info")
    return response


@require_POST
def mark_followed_up(request, pk: int):
    """Log a follow-up as done and push the next reminder out."""
    application = get_object_or_404(Application.objects.with_related(), pk=pk)
    application.log(EventKind.FOLLOW_UP, "Relance envoyée")
    application.follow_up_on = timezone.localdate() + dt.timedelta(
        days=settings.DEFAULT_FOLLOW_UP_DAYS
    )
    application.save(update_fields=["follow_up_on", "updated_at"])
    response = _status_response(request, application, request.POST.get("source", "detail"))
    return toast(
        response, f"Relance notée, prochaine le {application.follow_up_on:%d/%m/%Y}."
    )


@require_http_methods(["GET", "POST"])
def edit_notes(request, pk: int):
    """The personal scratchpad. Always editable, saved in place."""
    application = get_object_or_404(Application, pk=pk)
    saved = False
    form = NotesForm(instance=application)
    if request.method == "POST":
        form = NotesForm(request.POST, instance=application)
        if form.is_valid():
            form.save()
            form = NotesForm(instance=application)
            saved = True
    response = render(
        request,
        "tracker/partials/notes_panel.html",
        {"application": application, "notes_form": form},
    )
    return toast(response, "Notes enregistrées.") if saved else response


@require_http_methods(["GET", "POST"])
def add_event(request, pk: int):
    application = get_object_or_404(Application, pk=pk)
    form = EventForm()
    added = False
    if request.method == "POST":
        form = EventForm(request.POST)
        if form.is_valid():
            event = form.save(commit=False)
            event.application = application
            event.save()
            form = EventForm()
            added = True
    response = render(
        request,
        "tracker/partials/timeline_panel.html",
        {"application": application, "event_form": form, "expand_form": not added},
    )
    return toast(response, "Événement ajouté au fil.") if added else response


@require_POST
def delete_event(request, pk: int):
    event = get_object_or_404(ActivityEvent.objects.select_related("application"), pk=pk)
    application = event.application
    event.delete()
    response = render(
        request,
        "tracker/partials/timeline_panel.html",
        {"application": application, "event_form": EventForm()},
    )
    return toast(response, "Événement supprimé.", "info")


@require_http_methods(["GET", "POST"])
def add_document(request, pk: int | None = None):
    """Attach a file to an application, or drop one in the shared library."""
    application = get_object_or_404(Application, pk=pk) if pk else None
    form = DocumentForm()
    added = False
    if request.method == "POST":
        form = DocumentForm(request.POST, request.FILES)
        if form.is_valid():
            document = form.save(commit=False)
            document.application = application
            document.label = form.cleaned_data.get("label") or document.file.name
            if document.is_primary and application:
                application.documents.filter(kind=document.kind).update(is_primary=False)
            document.save()
            if application:
                application.log(EventKind.NOTE, f"Document ajouté : {document.label}")
            if not application:
                response = HttpResponse(status=204)
                response.headers["HX-Redirect"] = reverse("tracker:document_library")
                return response
            form = DocumentForm()
            added = True

    if application is None:
        return render(
            request,
            "tracker/partials/library_form.html",
            {"document_form": form},
            status=200 if request.method == "GET" else 422,
        )

    response = render(
        request,
        "tracker/partials/documents_panel.html",
        {"application": application, "document_form": form, "expand_form": not added},
    )
    return toast(response, "Document ajouté.") if added else response


@require_POST
def delete_document(request, pk: int):
    document = get_object_or_404(Document.objects.select_related("application"), pk=pk)
    application = document.application
    label = document.label
    document.delete()  # the post_delete signal removes the file from disk
    if application:
        response = render(
            request,
            "tracker/partials/documents_panel.html",
            {"application": application, "document_form": DocumentForm()},
        )
        return toast(response, f"« {label} » supprimé.", "info")
    response = HttpResponse(status=204)
    response.headers["HX-Redirect"] = reverse("tracker:document_library")
    return response


@require_http_methods(["GET", "POST"])
def add_contact(request, pk: int):
    application = get_object_or_404(Application, pk=pk)
    form = ContactForm()
    added = False
    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            contact = form.save(commit=False)
            contact.application = application
            contact.save()
            form = ContactForm()
            added = True
    response = render(
        request,
        "tracker/partials/contacts_panel.html",
        {"application": application, "contact_form": form, "expand_form": not added},
    )
    return toast(response, "Contact ajouté.") if added else response


@require_POST
def delete_contact(request, pk: int):
    contact = get_object_or_404(Contact.objects.select_related("application"), pk=pk)
    application = contact.application
    contact.delete()
    response = render(
        request,
        "tracker/partials/contacts_panel.html",
        {"application": application, "contact_form": ContactForm()},
    )
    return toast(response, "Contact supprimé.", "info")


@require_http_methods(["GET", "POST"])
def quick_create(request):
    if request.method == "POST":
        form = QuickApplicationForm(request.POST)
        if form.is_valid():
            application = form.save()
            application.log(EventKind.NOTE, "Offre repérée")
            response = HttpResponse(status=204)
            response.headers["HX-Redirect"] = application.get_absolute_url()
            return response
        return render(
            request,
            "tracker/partials/quick_form.html",
            {"quick_form": form, "company_names": company_names()},
            status=422,
        )
    return render(
        request,
        "tracker/partials/quick_form.html",
        {"quick_form": QuickApplicationForm(), "company_names": company_names()},
    )


@require_POST
def set_gap_status(request, pk: int):
    gap = get_object_or_404(SkillGap, pk=pk)
    new_status = request.POST.get("status")
    if new_status not in GapStatus.values:
        return HttpResponseBadRequest("État inconnu.")
    gap.status = new_status
    gap.save(update_fields=["status"])
    response = render(request, "tracker/partials/gap_card.html", {"gap": gap,
                                                                  "max_gap": request.POST.get("max_gap", 10)})
    return toast(response, f"{gap.name} : {GapStatus(new_status).label.lower()}.")


def stats_bar(request):
    """Small fragment refreshed after any status change."""
    return render(request, "tracker/partials/stats_bar.html", {"stats": dashboard_stats()})
