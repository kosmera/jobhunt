"""The questionnaire on the web: one URL per screen, plain forms, the machine in charge.

``/bienvenue/`` resolves the run and redirects to the current screen;
``/bienvenue/<slug>/`` renders a screen when it is the current one or a
visited one still on the path (that is the back arrow and the browser's
Back button), otherwise redirects to the current one. GET never writes the
run. POST carries ``action`` — ``continue``, ``upload``, ``skip``, ``edit`` —
and every action is validated by the machine *before* any side effect (the
account creation at the gate, the CV upload); a refused transition — a stale
tab, a forged form — becomes a message and a redirect, never a corrupted run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.contrib import auth, messages
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache

from accounts import conf
from accounts.forms import SignupForm
from accounts.middleware import local_auto_sign_in, onboarding_not_required
from accounts.onboarding import services, store
from accounts.onboarding.flow import (
    EDITABLE,
    EVERYTHING,
    OPTIONS,
    RADIUS_CHOICES,
    machine,
    salary_periods,
    salary_unit,
    step_url,
)
from accounts.onboarding.forms import (
    CitiesForm,
    CVForm,
    IdentityForm,
    IndustriesForm,
    LaunchInterestForm,
    MultiChoiceForm,
    SalaryForm,
    SingleChoiceForm,
    TitlesForm,
)
from accounts.onboarding.machine import Context, Event, IllegalTransition, Kind, Run, Step
from accounts.services import (
    LOCAL_BACKEND,
    create_local_user,
    finish_onboarding,
    name_profile,
    preferences_for,
    profile_for,
    sign_in_without_password,
)
from tracker.models import Language

STALE_TAB = "Reprends ici : cette page venait d'un autre onglet."
EXPIRED = "Ta session a expiré ou les cookies sont bloqués : on reprend au début."
NO_FILE = "Envoie un fichier, ou passe cette étape."
WELCOME = "Bienvenue, {name}. Le suivi est à toi."

REQUIRED_MESSAGES = {
    "help": "Choisis au moins une réponse.",
    "work_type": "Choisis au moins un type de contrat.",
}


@dataclass(frozen=True)
class Prepared:
    user: Any
    run: Run
    ctx: Context
    expired: bool


def _redirect(request, url: str) -> HttpResponse:
    """A redirect the upload script can follow without swallowing the messages.

    The CV screen posts through ``XMLHttpRequest`` to show the upload progress;
    an XHR follows a redirect by itself and that hidden GET would consume the
    flash messages before the visitor sees the page. Such a request gets a
    ``204`` and the address in a header; the script navigates there.
    """
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        response = HttpResponse(status=204)
        response.headers["X-Onboarding-Redirect"] = url
        return response
    return redirect(url)


def _prepare(request) -> Prepared | HttpResponse:
    """Who is asking, where their run stands — or where to send them instead."""
    user = request.user if request.user.is_authenticated else None
    if user is None and conf.is_local() and local_auto_sign_in(request):
        user = request.user
    if user is not None and profile_for(user).is_onboarded:
        store.clear(request)
        return redirect("tracker:dashboard")
    if user is None and not conf.is_local() and not conf.signup_open():
        # Nobody answers twenty questions for an account they cannot create.
        return redirect_to_login(reverse("accounts:onboarding"))
    run = store.load(request)
    expired = run is None
    if run is not None and user is None and machine.before("identity", run.state):
        # The account that crossed the gate is gone (deactivated, deleted) while
        # the browser kept its session: back to the gate, answers kept.
        run = machine.rewind_to(run, "identity")
    return Prepared(user, run or machine.fresh(), services.build_context(user), expired)


@login_not_required
@onboarding_not_required
@require_http_methods(["GET", "POST"])
def onboarding(request):
    """Entry point (``/bienvenue/``, the URL the middleware and the landing page know)."""
    prepared = _prepare(request)
    if isinstance(prepared, HttpResponse):
        return prepared
    return redirect(step_url(machine.current(prepared.run, prepared.ctx)))


@onboarding_not_required
@never_cache
@require_http_methods(["GET"])
def onboarding_cv_status(request):
    """Poll only the signed-in visitor's current onboarding CV, without writes to the session."""
    run = store.load(request)
    if run is None:
        raise Http404
    ctx = services.build_context(request.user)
    card = services.cv_card(request.user, run.answers, ctx)
    if card is None or card.analysis is None:
        raise Http404
    requested_document = request.GET.get("document")
    if requested_document is not None and _int_or_none(requested_document) != card.document.pk:
        return HttpResponse(status=409)
    return render(request, "accounts/onboarding/partials/cv_analysis.html", {"card": card, "analysis": card.analysis})


@login_not_required
@onboarding_not_required
@require_http_methods(["GET", "POST"])
def onboarding_step(request, slug: str):
    step = machine.by_slug.get(slug)
    if step is None:
        raise Http404
    prepared = _prepare(request)
    if isinstance(prepared, HttpResponse):
        return prepared
    user, run, ctx, expired = prepared.user, prepared.run, prepared.ctx, prepared.expired
    current = machine.current(run, ctx)
    if not machine.reachable(run, step.id, ctx):
        if request.method == "POST":
            messages.warning(request, EXPIRED) if expired else messages.info(request, STALE_TAB)
            return _redirect(request, step_url(current))
        return redirect(step_url(current))

    if request.method != "POST":
        return _render(request, step, run, ctx, user, _form_for(step, request, run, user))

    action = request.POST.get("action", "continue")
    try:
        if action == "skip":
            new_run = machine.apply(run, Event.SKIP, ctx, at=step.id)
        elif action == "edit":
            target = machine.by_slug.get(request.POST.get("target", ""))
            target_id = target.id if target is not None and target.id in EDITABLE else None
            new_run = machine.apply(run, Event.EDIT, ctx, at=step.id, target=target_id)
        elif action in ("continue", "upload"):
            event = Event.STAY if action == "upload" else Event.CONTINUE
            machine.check_event(run, event, ctx, at=step.id)  # before any side effect
            if step.kind is Kind.FILE and event is Event.CONTINUE and not machine.answered(step.id, run.answers):
                messages.warning(request, NO_FILE)
                return _redirect(request, step_url(step.id))
            form = _form_for(step, request, run, user, data=request.POST, files=request.FILES, upload=action == "upload")
            if form is not None and not form.is_valid():
                return _render(request, step, run, ctx, user, form)
            if step.kind is Kind.GATE and user is None and conf.passwordless():
                from accounts.views import queue_email_link

                assert form is not None
                new_run = machine.apply(run, event, ctx, at=step.id, answer={"display_name": form.cleaned_data["display_name"]})
                queue_email_link(
                    request, form.cleaned_data["email"], purpose="signup", display_name=form.cleaned_data["display_name"],
                    onboarding_data=new_run.to_json(), next_path=reverse("accounts:onboarding"),
                )
                return _redirect(request, reverse("accounts:email_link_sent"))
            answer = _answer(step, form, request, ctx, user)
            new_run = machine.apply(run, event, ctx, at=step.id, answer=answer)
            if step.id == "identity":
                # The page was validated with the gate on the path; the redirect
                # uses the context of the account that now exists.
                user = request.user
                ctx = services.build_context(user)
        else:
            return HttpResponseBadRequest("Action inconnue.")
    except IllegalTransition:
        messages.info(request, STALE_TAB)
        return _redirect(request, step_url(current))

    if new_run.state == machine.terminal:
        assert user is not None  # the gate precedes the plan on every path (check() + guard)
        profile = finish_onboarding(user, new_run.answers)
        store.clear(request)
        messages.success(request, WELCOME.format(name=profile.display_name))
        if conf.collect_launch_interest() and new_run.answers.get("launch_notify") is True:
            messages.success(request, "C'est noté ! Ton intérêt est enregistré. "
                             "Tu recevras un e-mail au lancement. Aucun abonnement n'a été activé.")
        return _redirect(request, reverse("tracker:dashboard"))
    store.save(request, new_run)
    return _redirect(request, step_url(machine.current(new_run, ctx)))


# ---------------------------------------------------------------------------
# Forms and answers per kind of screen
# ---------------------------------------------------------------------------


def _form_for(step: Step, request, run: Run, user, *, data=None, files=None, upload: bool = False):
    """The form of a screen, bound to ``data`` or pre-filled from the run; ``None`` for screens without one."""
    answers = run.answers
    kind = step.kind
    if kind is Kind.SINGLE:
        key = step.answer_keys[0]
        return SingleChoiceForm(data, key=key, options=OPTIONS[step.id], initial={"choice": answers.get(key)})
    if kind is Kind.MULTI:
        key = step.answer_keys[0]
        return MultiChoiceForm(
            data, key=key, options=OPTIONS[step.id],
            required_message=REQUIRED_MESSAGES.get(step.id, "Choisis au moins une réponse."),
            initial={"choice": answers.get(key)},
        )
    if kind is Kind.CHIPS:
        return TitlesForm(data, initial={"titles": answers.get("job_titles")})
    if kind is Kind.GRID:
        return IndustriesForm(
            data, initial={"industries": answers.get("industries"), "any_industry": answers.get("any_industry")}
        )
    if kind is Kind.CITIES:
        radius = answers.get("search_radius_km")
        return CitiesForm(
            data,
            initial={
                "cities": answers.get("cities"),
                "search_radius_km": radius if isinstance(radius, int) else services.default_radius(user),
            },
        )
    if kind is Kind.SALARY:
        return SalaryForm(
            data,
            initial={
                "salary_period": answers.get("salary_period") or "month",
                "salary_min": answers.get("salary_min"),
            },
        )
    if kind is Kind.GATE:
        if services.gate_variant(user, services.build_context(user)) == "accounts_signup":
            return SignupForm(data)
        known = profile_for(user).display_name if user is not None else ""
        return IdentityForm(data, initial={"display_name": answers.get("display_name") or known})
    if kind is Kind.FILE:
        if data is not None and not upload:
            return None  # a plain Continue on the result card carries no form
        language = answers.get("cv_language") or (preferences_for(user).default_cv_language if user else "fr")
        return CVForm(data, files, initial={"language": language})
    if kind is Kind.PLAN and conf.collect_launch_interest():
        assert user is not None
        return LaunchInterestForm(data, initial={"email": user.email})
    return None


def _answer(step: Step, form, request, ctx: Context, user) -> dict[str, Any]:
    """What the machine stores for this POST — after the screen's side effects, if any."""
    if step.kind is Kind.PLAN and not conf.collect_launch_interest():
        return dict(step.skip_values or {})
    if step.kind is Kind.GATE:
        assert form is not None
        variant = services.gate_variant(user, ctx)
        name = form.cleaned_data["display_name"]
        if variant == "accounts_signup":
            account = form.save()  # names the profile on its own behalf; no completion
            auth.login(request, account, backend=LOCAL_BACKEND)
        elif variant == "local_new":
            account = create_local_user(name)
            sign_in_without_password(request, account)
            name_profile(account, name)
        else:
            name_profile(request.user, name)
        return {"display_name": name}
    if step.kind is Kind.FILE:
        if form is None:
            return {}
        return services.store_cv(
            request,
            upload=form.cleaned_data["file"],
            language=form.cleaned_data["language"],
            replacing=_int_or_none(request.POST.get("replacing", "")),
        )
    return form.answers() if form is not None else {}


def _int_or_none(raw: str) -> int | None:
    """A positive integer from a form value, or nothing (``str.isdigit`` accepts « ² »)."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render(request, step: Step, run: Run, ctx: Context, user, form, status: int = 200) -> HttpResponse:
    n, total = machine.position(step.id, run.answers, ctx)
    back = machine.back_target(run, step.id, ctx)
    editing = run.return_to is not None and step.id != run.return_to
    context: dict[str, Any] = {
        "step": step,
        "form": form,
        "n": n,
        "total": total,
        "back_url": step_url(back) if back else "",
        "editing": editing,
        "continue_label": "Enregistrer et revenir au bilan" if editing else "Continuer",
        "answers": run.answers,
        "options": OPTIONS.get(step.id, ()),
        "everything": EVERYTHING,
    }
    kind = step.kind
    if step.id == "welcome_back" or kind is Kind.GATE:
        context["waiting"] = services.waiting_counts(user) if user is not None else {"applications": 0, "documents": 0}
    if kind is Kind.CHIPS:
        picked = getattr(form, "titles", None) or []
        context.update({
            "picked": picked,
            "suggestions": list(services.data.JOB_TITLES),
            "recommended": services.related_titles(picked),
        })
    elif kind is Kind.GRID:
        selected = getattr(form, "industries", None) or []
        primary, more = services.industry_cards(selected)
        context.update({
            "primary_cards": primary,
            "more_cards": more,
            "more_open": any(card["checked"] for card in more),
            "any_industry": bool(form["any_industry"].value()) if form is not None else False,
        })
    elif kind is Kind.CITIES:
        context.update({
            "picked": getattr(form, "cities", None) or [],
            "suggestions": [[name, province] for name, province in services.data.CITIES],
            "radius_choices": RADIUS_CHOICES,
        })
    elif kind is Kind.SALARY:
        period = form["salary_period"].value() if form is not None else ""
        context.update({"periods": salary_periods(), "unit": salary_unit(period or "")})
    elif kind is Kind.REVIEW:
        context["rows"] = services.review_rows(run.answers, ctx)
    elif kind is Kind.GATE:
        context["variant"] = services.gate_variant(user, ctx)
        context["login_url"] = f"{reverse('accounts:login')}?next={reverse('accounts:onboarding')}"
    elif kind is Kind.FILE:
        card = services.cv_card(user, run.answers, ctx) if user is not None else None
        replacing = request.GET.get("remplacer") == "1"
        context.update({
            "card": card,
            "replacing": replacing and card is not None,
            "language_choices": Language.choices,
            "ai_plugin": ctx.ai_plugin,
        })
    elif kind is Kind.PLAN:
        assert user is not None
        if conf.collect_launch_interest():
            context.update({"premium_price_label": "24,90 €", "premium_price_period": "/ mois"})
            return render(request, "accounts/onboarding/interest.html", context, status=status)
        context.update({"rows": services.plan_rows(preferences_for(user)), "cost": services.cost_line(run.answers)})
    template = step.template or f"accounts/onboarding/{kind.value}.html"
    return render(request, template, context, status=status)
