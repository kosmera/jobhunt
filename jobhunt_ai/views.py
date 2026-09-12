"""Vues du copilote. Pages complètes + fragments HTMX, comme dans le cœur."""

from __future__ import annotations

import json

import rls
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from accounts.services import owned_or_404
from jobhunt_ai.access import premium_required
from jobhunt_ai.forms import CVUploadForm, ScoutForm
from jobhunt_ai.hooks import CopilotCVAnalyzer
from jobhunt_ai.models import (
    AgentRun,
    CandidateProfile,
    GeneratedCV,
    LeadStatus,
    MatchReport,
    OfferLead,
    RunKind,
    RunStatus,
)
from jobhunt_ai.services import runner
from jobhunt_ai.services.applications import import_lead
from jobhunt_ai.services.documents import UnsupportedFormat, prepare_cv
from jobhunt_ai.settings import get
from tracker.models import Application
from tracker.privacy import AnonymizedText


def toast(response: HttpResponse, message: str, tone: str = "success") -> HttpResponse:
    """Même mécanique que le cœur : un toast via l'en-tête HX-Trigger."""
    payload = {"toast": {"message": message, "tone": tone}}
    existing = response.headers.get("HX-Trigger")
    if existing:
        merged = json.loads(existing)
        merged.update(payload)
        payload = merged
    response.headers["HX-Trigger"] = json.dumps(payload)
    return response


def _running(user, kind: str | None = None, application=None):
    # Deadlines survive web server restarts and are shared across workers.
    runner.sweep_orphans(user)
    queryset = AgentRun.objects.filter(
        owner=user, status__in=[RunStatus.PENDING, RunStatus.RUNNING]
    )
    if kind:
        queryset = queryset.filter(kind=kind)
    if application is not None:
        queryset = queryset.filter(application=application)
    return queryset.first()


def _run_fragment(
    request, run: AgentRun, status: int = 200, panel: bool = False
) -> HttpResponse:
    """Le drapeau ``panel`` suit le fragment de bout en bout : il décide si la
    fin d'exécution rafraîchit tout ou seulement le panneau de la candidature."""
    if run.status in (RunStatus.FAILED, RunStatus.SUCCEEDED) and run.result.get("quota"):
        return render(
            request, "jobhunt_ai/partials/quota_exceeded.html",
            {"run": run, "quota": run.result["quota"], "upgrade_url": get("UPGRADE_URL")},
        )
    return render(
        request,
        "jobhunt_ai/partials/run_status.html",
        {"run": run, "panel": panel},
        status=status,
    )


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@login_required
@premium_required
def copilot(request):
    user = request.user
    profile = CandidateProfile.primary(user)
    runs = AgentRun.objects.filter(owner=user).select_related("application")[:8]
    return render(
        request,
        "jobhunt_ai/copilot.html",
        {
            "page": "copilot",
            "profile": profile,
            "profiles_count": CandidateProfile.objects.filter(owner=user).count(),
            "runs": runs,
            "active_run": _running(user),
            "new_leads": OfferLead.objects.filter(
                owner=user, status=LeadStatus.NEW
            ).count(),
            "reports_count": MatchReport.objects.filter(
                application__owner=user
            ).count(),
            "generated_count": GeneratedCV.objects.filter(
                application__owner=user
            ).count(),
        },
    )


@login_required
@premium_required
def profile_page(request):
    profiles = list(CandidateProfile.objects.filter(owner=request.user))
    profile = next(
        (p for p in profiles if p.is_primary), profiles[0] if profiles else None
    )
    requested = request.GET.get("profil")
    if requested and requested.isdigit():
        profile = next((p for p in profiles if p.pk == int(requested)), profile)
    return render(
        request,
        "jobhunt_ai/profile.html",
        {
            "page": "copilot",
            "profiles": profiles,
            "profile": profile,
            "upload_form": CVUploadForm(user=request.user),
            "active_run": _running(request.user, RunKind.PARSE_CV),
        },
    )


@login_required
@premium_required
def leads_page(request):
    user = request.user
    scope = request.GET.get("etat") or LeadStatus.NEW
    if scope not in LeadStatus.values:
        scope = LeadStatus.NEW
    mine = OfferLead.objects.filter(owner=user)
    leads = mine.filter(status=scope)
    counts = {
        status: mine.filter(status=status).count() for status in LeadStatus.values
    }
    return render(
        request,
        "jobhunt_ai/leads.html",
        {
            "page": "copilot",
            "leads": leads,
            "scope": scope,
            "counts": counts,
            "scout_form": ScoutForm(user=user),
            "active_run": _running(user, RunKind.SCOUT),
            "has_profile": _has_profile(user),
            "lead_statuses": LeadStatus,
        },
    )


def _has_profile(user) -> bool:
    return CandidateProfile.objects.filter(owner=user).exists()


# ---------------------------------------------------------------------------
# Fragments HTMX
# ---------------------------------------------------------------------------


@login_required
@premium_required
@require_GET
def run_status(request, pk: int):
    runner.sweep_orphans(request.user, run_id=pk)
    run = owned_or_404(
        AgentRun.objects.select_related("application").defer(
            "params", "fanout_context"
        ),
        request.user,
        pk=pk,
    )
    if run.result.get("quota") and run.status in (RunStatus.SUCCEEDED, RunStatus.FAILED):
        return _run_fragment(request, run)
    if run.status == RunStatus.SUCCEEDED and request.GET.get("poll"):
        # Sondage depuis le panneau d'une candidature : ne rafraîchir que le
        # panneau, sinon le rechargement effacerait des notes en cours de
        # frappe ailleurs sur la fiche.
        if request.GET.get("panel") and run.application_id:
            response = _application_panel(request, run.application)
            response.headers["HX-Retarget"] = "#ai-panel"
            response.headers["HX-Reswap"] = "outerHTML"
            if run.kind == RunKind.MATCH:
                return toast(
                    response,
                    f"Compatibilité estimée : {run.result.get('score', '?')} %",
                )
            return toast(response, "CV généré et rangé avec la candidature.")
        # Ailleurs (profil, pistes, tableau de bord) : la page ne porte pas
        # de saisie en cours, un rechargement complet est le plus simple.
        response = HttpResponse(status=204)
        response.headers["HX-Refresh"] = "true"
        return response
    return _run_fragment(request, run, panel=bool(request.GET.get("panel")))


API_STATUSES = {
    RunStatus.PENDING: "PENDING",
    RunStatus.RUNNING: "PROCESSING",
    RunStatus.SUCCEEDED: "SUCCESS",
    RunStatus.FAILED: "FAILED",
}


def _run_json(run: AgentRun, *, status=200) -> JsonResponse:
    status_url = reverse("jobhunt_ai:api_run_status", args=[run.pk])
    response = JsonResponse(
        {
            "id": run.pk,
            "kind": run.kind,
            "status": API_STATUSES[run.status],
            "phase": run.phase,
            "progress": {"current": run.progress_current, "total": run.progress_total},
            "created_at": run.created_at,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "deadline_at": run.deadline_at,
            "result": run.result if run.status == RunStatus.SUCCEEDED else None,
            "error": run.error if run.status == RunStatus.FAILED else None,
            "quota": run.result.get("quota"),
            "status_url": status_url,
            "leads_url": reverse("jobhunt_ai:api_run_leads", args=[run.pk]),
        },
        status=status,
    )
    response.headers["Cache-Control"] = "private, no-store"
    if not run.is_finished:
        response.headers["Retry-After"] = "3"
    if status == 202:
        response.headers["Location"] = status_url
    return response


@login_required
@premium_required
@require_POST
def api_scout_start(request):
    """Return 202 after durable submission; never wait for a task result."""
    if request.content_type == "application/json":
        try:
            data = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({"error": "JSON invalide."}, status=400)
        if not isinstance(data, dict):
            return JsonResponse({"error": "Un objet JSON est requis."}, status=400)
    else:
        data = request.POST
    form = ScoutForm(data, user=request.user)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
    profile = CandidateProfile.primary(request.user)
    if profile is None:
        return JsonResponse(
            {"error": "Analyse d'abord un CV pour créer ton profil."}, status=409
        )
    try:
        run = runner.launch(
            RunKind.SCOUT,
            owner=request.user,
            profile=profile,
            params=form.cleaned_data,
        )
    except DatabaseError:
        # launch's transaction rolled back both run and message. No 202 for
        # work that was not accepted. Keep database details out of the API.
        response = JsonResponse({"error": "File indisponible, réessaie."}, status=503)
        response.headers["Retry-After"] = "10"
        return response
    return _run_json(run, status=202)


@login_required
@premium_required
@require_GET
def api_run_status(request, pk: int):
    runner.sweep_orphans(request.user, run_id=pk)
    run = owned_or_404(
        AgentRun.objects.defer("params", "fanout_context"),
        request.user,
        pk=pk,
    )
    return _run_json(run)


@login_required
@premium_required
@require_GET
def api_run_leads(request, pk: int):
    owned_or_404(AgentRun.objects.only("id", "owner_id"), request.user, pk=pk)
    leads = (
        OfferLead.objects.filter(owner=request.user, run_id=pk)
        .order_by("-score", "-pk")
        .values(
            "id",
            "title",
            "company_name",
            "location",
            "url",
            "source_name",
            "status",
            "score",
            "score_reason",
            "score_confidence",
            "score_blockers",
        )
    )
    page = Paginator(leads, 50).get_page(request.GET.get("page"))
    response = JsonResponse(
        {
            "results": list(page),
            "count": page.paginator.count,
            "next_page": page.next_page_number() if page.has_next() else None,
        }
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


#: Ce que voit l'utilisateur quand le cœur range un CV sans l'analyser :
#: format illisible, ou trop peu de texte extrait (un scan). Il n'y a plus
#: de repli qui enverrait le PDF au modèle — ce serait lui rendre exactement
#: ce que l'anonymisation vient de masquer.
UNREADABLE_CV = (
    "Ce CV est bien rangé dans tes documents, mais il n'en sort pas assez de "
    "texte pour être analysé. Un scan sans couche texte doit d'abord passer "
    "par une reconnaissance de caractères ; un DOCX, un TXT ou un MD passe "
    "toujours."
)


def _upload_error(request, form: CVUploadForm, message: str) -> HttpResponse:
    form.add_error(None, message)
    return render(
        request,
        "jobhunt_ai/partials/upload_form.html",
        {"upload_form": form},
        status=422,
    )


@login_required
@premium_required
@require_POST
def parse_cv(request):
    """Prepare anonymized intake through the storage port, then launch the run."""
    form = CVUploadForm(request.POST, request.FILES, user=request.user)
    if not form.is_valid():
        return render(
            request,
            "jobhunt_ai/partials/upload_form.html",
            {"upload_form": form},
            status=422,
        )

    analyzer = CopilotCVAnalyzer(
        make_primary=form.cleaned_data.get("make_primary", False)
    )
    try:
        submission = prepare_cv(
            request.user,
            document=form.cleaned_data.get("document"),
            upload=form.cleaned_data.get("file"),
            label=form.cleaned_data.get("label", ""),
        )
    except UnsupportedFormat as exc:
        return _upload_error(request, form, str(exc))
    except OSError as exc:
        return _upload_error(request, form, f"Fichier illisible : {exc}")
    if submission is not None:
        analyzer.analyze_cv(
            request.user,
            document_id=submission.document_id,
            label=submission.label,
            language=submission.language,
            text=AnonymizedText(submission.text, submission.redactions),
        )

    if analyzer.run is None:
        return _upload_error(request, form, UNREADABLE_CV)
    return _run_fragment(request, analyzer.run)


@login_required
@premium_required
@require_POST
def evaluate(request, pk: int):
    application = owned_or_404(
        Application.objects.select_related("company"), request.user, pk=pk
    )
    if not _has_profile(request.user):
        response = _application_panel(request, application)
        return toast(response, "Analyse d'abord un CV pour créer ton profil.", "error")
    existing = _running(request.user, application=application)
    if existing:
        return _run_fragment(request, existing, panel=True)
    run = runner.launch(RunKind.MATCH, owner=request.user, application=application)
    return _run_fragment(request, run, panel=True)


@login_required
@premium_required
@require_POST
def generate_cv(request, pk: int):
    application = owned_or_404(
        Application.objects.select_related("company"), request.user, pk=pk
    )
    if not _has_profile(request.user):
        response = _application_panel(request, application)
        return toast(response, "Analyse d'abord un CV pour créer ton profil.", "error")
    existing = _running(request.user, application=application)
    if existing:
        return _run_fragment(request, existing, panel=True)
    language = request.POST.get("language") or application.cv_language
    run = runner.launch(
        RunKind.GENERATE_CV,
        owner=request.user,
        params={"language": language},
        application=application,
    )
    return _run_fragment(request, run, panel=True)


def _application_panel(request, application) -> HttpResponse:
    return render(
        request,
        "jobhunt_ai/partials/application_panel.html",
        {"application": application},
    )


@login_required
@premium_required
@require_POST
def scout_start(request):
    form = ScoutForm(request.POST, user=request.user)
    if not form.is_valid():
        return render(
            request,
            "jobhunt_ai/partials/scout_form.html",
            {"scout_form": form, "has_profile": _has_profile(request.user)},
            status=422,
        )
    if not _has_profile(request.user):
        return HttpResponseBadRequest("Analyse d'abord un CV pour créer ton profil.")
    if existing := _running(request.user, RunKind.SCOUT):
        return _run_fragment(request, existing)
    run = runner.launch(
        RunKind.SCOUT,
        owner=request.user,
        params={
            "keywords": form.cleaned_data.get("keywords", ""),
            "location": form.cleaned_data["location"],
            "radius_km": form.cleaned_data["radius_km"],
        },
    )
    return _run_fragment(request, run)


@login_required
@premium_required
@require_POST
def lead_import(request, pk: int):
    with rls.as_user(request.user.pk), transaction.atomic():
        lead = owned_or_404(OfferLead.objects.select_for_update(), request.user, pk=pk)
        if lead.status == LeadStatus.IMPORTED and lead.application_id:
            return _lead_row(request, lead)

        application = import_lead(request.user, lead)

        lead.status = LeadStatus.IMPORTED
        lead.application = application
        lead.save(update_fields=["status", "application"])
    response = _lead_row(request, lead)
    return toast(response, f"« {lead.title} » ajoutée au backlog.")


@login_required
@premium_required
@require_POST
def lead_dismiss(request, pk: int):
    lead = owned_or_404(OfferLead, request.user, pk=pk)
    lead.status = LeadStatus.DISMISSED
    lead.save(update_fields=["status"])
    response = _lead_row(request, lead)
    return toast(response, "Piste écartée.", "info")


@login_required
@premium_required
@require_POST
def lead_restore(request, pk: int):
    lead = owned_or_404(OfferLead, request.user, pk=pk)
    lead.status = LeadStatus.NEW
    lead.save(update_fields=["status"])
    response = _lead_row(request, lead)
    return toast(response, "Piste remise à trier.", "info")


def _lead_row(request, lead: OfferLead) -> HttpResponse:
    return render(request, "jobhunt_ai/partials/lead_row.html", {"lead": lead})


def _profile_redirect() -> HttpResponse:
    """Les boutons de gestion de profil sont en hx-post : rechargement propre."""
    response = HttpResponse(status=204)
    response.headers["HX-Redirect"] = reverse("jobhunt_ai:profile")
    return response


@login_required
@premium_required
@require_POST
def profile_set_primary(request, pk: int):
    profile = owned_or_404(CandidateProfile, request.user, pk=pk)
    profile.is_primary = True
    profile.save()
    return _profile_redirect()


@login_required
@premium_required
@require_POST
def profile_delete(request, pk: int):
    profile = owned_or_404(CandidateProfile, request.user, pk=pk)
    profile.delete()
    return _profile_redirect()
