"""Views. Full pages render templates; HTMX endpoints return fragments.

This is the web side of the hexagon: a view parses the request, hands the
work to ``tracker.queries`` (what a page lists) or ``tracker.services``
(what an action does), and renders. Single rows come from the persistence
ports, scoped on the signed-in account — another account's row is a 404,
converted from ``NotFound`` in exactly one place (``or_404``). Thresholds
come from ``request.preferences``; the use cases never read them.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods

from tracker import queries, services
from tracker.adapters import persistence
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
from tracker.models import PIPELINE_STATUSES, Application, EventKind, Status
from tracker.ports import NotFound


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


def or_404(fetch, *args, **kwargs):
    """Run a port or service lookup; a row that is not the account's is a 404."""
    try:
        return fetch(*args, **kwargs)
    except NotFound as exc:
        raise Http404(str(exc)) from exc


def _application(request, pk: int) -> Application:
    return or_404(persistence().applications.get, request.user, pk)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def dashboard(request):
    today = timezone.localdate()
    user = request.user
    preferences = request.preferences

    attention = services.attention(user, stale_days=preferences.stale_after_days, today=today)
    lists = queries.dashboard_lists(user, today=today)

    stats = services.dashboard_stats(user, today=today)
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
            **lists,
            "quick_form": QuickApplicationForm(user=user),
            "company_names": queries.company_names(user),
            "stale_days": preferences.stale_after_days,
        },
    )


def pipeline(request):
    user = request.user
    return render(
        request,
        "tracker/pipeline.html",
        {
            "page": "pipeline",
            "board": services.board(user),
            "stats": services.dashboard_stats(user),
            "closed": queries.closed(user),
        },
    )


def application_list(request):
    form = ApplicationFilterForm(request.GET or None)
    data = form.cleaned_data if form.is_valid() else {}
    rows = queries.filtered_applications(request.user, data)
    context = {
        "page": "list",
        "filter_form": form,
        "applications": rows,
        "total": len(rows),
        "status_choices": Status.choices,
        "selected_statuses": data.get("status", []),
    }
    if request.headers.get("HX-Request"):
        return render(request, "tracker/partials/application_table.html", context)
    context["quick_form"] = QuickApplicationForm(user=request.user)
    context["company_names"] = queries.company_names(request.user)
    return render(request, "tracker/application_list.html", context)


def application_detail(request, pk: int):
    application = or_404(persistence().applications.detail, request.user, pk)
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
            "stale_days": request.preferences.stale_after_days,
        },
    )


def application_create(request):
    if request.method == "POST":
        form = ApplicationForm(request.POST, user=request.user)
        if form.is_valid():
            with persistence().atomic():
                application = form.save()
                services.record_application(application, "Candidature créée")
            messages.success(request, f"« {application.title} » ajoutée au suivi.")
            return redirect(application.get_absolute_url())
    else:
        form = ApplicationForm(
            user=request.user,
            initial={"status": Status.BACKLOG, "discovered_on": timezone.localdate()},
        )
    return render(
        request,
        "tracker/application_form.html",
        {
            "page": "list",
            "form": form,
            "title": "Nouvelle candidature",
            "company_names": queries.company_names(request.user),
        },
    )


def application_update(request, pk: int):
    application = _application(request, pk)
    previous_status = application.status
    if request.method == "POST":
        form = ApplicationForm(request.POST, instance=application, user=request.user)
        if form.is_valid():
            application = form.save()
            if application.status != previous_status:
                # The editor bypasses the transition rules on purpose (dates
                # are typed by hand); the timeline still records the move.
                persistence().events.add(
                    application,
                    EventKind.STATUS,
                    f"{Status(previous_status).label} → {Status(application.status).label}",
                )
            messages.success(request, "Candidature mise à jour.")
            return redirect(application.get_absolute_url())
    else:
        form = ApplicationForm(instance=application, user=request.user)
    return render(
        request,
        "tracker/application_form.html",
        {
            "page": "list",
            "form": form,
            "application": application,
            "title": f"Modifier — {application.company.name}",
            "company_names": queries.company_names(request.user),
        },
    )


@require_POST
def application_delete(request, pk: int):
    application = or_404(services.delete_application, request.user, pk)
    messages.success(request, f"« {application} » supprimée.")
    return redirect("tracker:application_list")


def document_library(request):
    return render(
        request,
        "tracker/documents.html",
        {
            "page": "documents",
            "library_form": DocumentForm(),
            **queries.document_library(request.user),
        },
    )


def document_download(request, pk: int):
    """Files never leave ``MEDIA_ROOT`` by URL: this is the only way out, and
    it checks who is asking."""
    document = or_404(persistence().documents.get, request.user, pk)
    try:
        handle = document.file.open("rb")
    except (FileNotFoundError, ValueError):
        raise Http404("Fichier introuvable sur le disque.")
    return FileResponse(handle, as_attachment=True, filename=Path(document.file.name).name)


def insights(request):
    user = request.user
    return render(
        request,
        "tracker/insights.html",
        {
            "page": "insights",
            **queries.insights(user, request.preferences.search_radius_km),
            "stats": services.dashboard_stats(user),
        },
    )


# ---------------------------------------------------------------------------
# HTMX endpoints
# ---------------------------------------------------------------------------


def _status_response(request, application: Application, source: str) -> HttpResponse:
    """Re-render whatever fragment the change was triggered from."""
    if source == "board":
        return render(
            request, "tracker/partials/board.html", {"board": services.board(request.user)}
        )
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
            "stale_days": request.preferences.stale_after_days,
        },
    )


@require_POST
def set_status(request, pk: int):
    application = _application(request, pk)
    new_status = request.POST.get("status", "")
    if new_status not in Status.values:
        return HttpResponseBadRequest("Statut inconnu.")

    changed = services.change_status(
        application,
        new_status,
        note=request.POST.get("note", ""),
        follow_up_days=request.preferences.follow_up_days,
    )
    source = request.POST.get("source") or request.GET.get("source") or "detail"
    response = _status_response(request, application, source)
    response.headers["HX-Trigger-After-Settle"] = json.dumps({"stats-changed": True})
    if changed:
        toast(response, f"{application.company.name} → {Status(new_status).label}")
    return response


@require_POST
def advance_status(request, pk: int):
    application = _application(request, pk)
    source = request.POST.get("source", "board")
    nxt = services.advance(application, follow_up_days=request.preferences.follow_up_days)
    if not nxt:
        response = _status_response(request, application, source)
        return toast(response, "Cette candidature est déjà au bout du parcours.", "info")
    response = _status_response(request, application, source)
    response.headers["HX-Trigger-After-Settle"] = json.dumps({"stats-changed": True})
    return toast(response, f"{application.company.name} → {Status(nxt).label}")


@require_POST
def set_follow_up(request, pk: int):
    application = _application(request, pk)
    raw = (request.POST.get("follow_up_on") or "").strip()
    preset = request.POST.get("in_days")

    if preset:
        try:
            on = timezone.localdate() + dt.timedelta(days=int(preset))
        except ValueError:
            return HttpResponseBadRequest("Délai invalide.")
    elif raw:
        try:
            on = dt.date.fromisoformat(raw)
        except ValueError:
            return HttpResponseBadRequest("Date invalide.")
    else:
        on = None

    services.schedule_follow_up(application, on)
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
    application = _application(request, pk)
    next_on = services.mark_followed_up(
        application, follow_up_days=request.preferences.follow_up_days
    )
    response = _status_response(request, application, request.POST.get("source", "detail"))
    return toast(response, f"Relance notée, prochaine le {next_on:%d/%m/%Y}.")


@require_http_methods(["GET", "POST"])
def edit_notes(request, pk: int):
    """The personal scratchpad. Always editable, saved in place."""
    application = _application(request, pk)
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
    application = _application(request, pk)
    form = EventForm()
    added = False
    if request.method == "POST":
        form = EventForm(request.POST)
        if form.is_valid():
            services.add_event(application, form.save(commit=False))
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
    application = or_404(services.delete_event, request.user, pk)
    response = render(
        request,
        "tracker/partials/timeline_panel.html",
        {"application": application, "event_form": EventForm()},
    )
    return toast(response, "Événement supprimé.", "info")


@require_http_methods(["GET", "POST"])
def add_document(request, pk: int | None = None):
    """Attach a file to an application, or drop one in the shared library."""
    application = _application(request, pk) if pk else None
    form = DocumentForm()
    added = False
    if request.method == "POST":
        form = DocumentForm(request.POST, request.FILES)
        if form.is_valid():
            document = form.save(commit=False)
            services.attach_document(
                request.user,
                application,
                document,
                label=form.cleaned_data.get("label") or document.file.name,
            )
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
    document = or_404(services.delete_document, request.user, pk)
    application = document.application
    if application:
        response = render(
            request,
            "tracker/partials/documents_panel.html",
            {"application": application, "document_form": DocumentForm()},
        )
        return toast(response, f"« {document.label} » supprimé.", "info")
    response = HttpResponse(status=204)
    response.headers["HX-Redirect"] = reverse("tracker:document_library")
    return response


@require_http_methods(["GET", "POST"])
def add_contact(request, pk: int):
    application = _application(request, pk)
    form = ContactForm()
    added = False
    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            services.add_contact(application, form.save(commit=False))
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
    application = or_404(services.delete_contact, request.user, pk)
    response = render(
        request,
        "tracker/partials/contacts_panel.html",
        {"application": application, "contact_form": ContactForm()},
    )
    return toast(response, "Contact supprimé.", "info")


@require_http_methods(["GET", "POST"])
def quick_create(request):
    if request.method == "POST":
        form = QuickApplicationForm(request.POST, user=request.user)
        if form.is_valid():
            with persistence().atomic():
                application = form.save()
                services.record_application(application, "Offre repérée")
            response = HttpResponse(status=204)
            response.headers["HX-Redirect"] = application.get_absolute_url()
            return response
        return render(
            request,
            "tracker/partials/quick_form.html",
            {"quick_form": form, "company_names": queries.company_names(request.user)},
            status=422,
        )
    return render(
        request,
        "tracker/partials/quick_form.html",
        {
            "quick_form": QuickApplicationForm(user=request.user),
            "company_names": queries.company_names(request.user),
        },
    )


@require_POST
def set_gap_status(request, pk: int):
    new_status = request.POST.get("status")
    try:
        gap = or_404(services.set_gap_status, request.user, pk, new_status)
    except ValueError:
        return HttpResponseBadRequest("État inconnu.")
    response = render(request, "tracker/partials/gap_card.html", {"gap": gap,
                                                                  "max_gap": request.POST.get("max_gap", 10)})
    return toast(response, f"{gap.name} : {gap.get_status_display().lower()}.")


def stats_bar(request):
    """Small fragment refreshed after any status change."""
    return render(
        request,
        "tracker/partials/stats_bar.html",
        {"stats": services.dashboard_stats(request.user)},
    )
