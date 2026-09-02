"""Onboarding, sign-in, sign-up, sign-out and the settings page."""

from __future__ import annotations

from django.conf import settings
from django.contrib import auth, messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_not_required
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from accounts import conf
from accounts.forms import (
    DeleteAccountForm,
    LoginForm,
    OnboardingForm,
    PreferencesForm,
    ProfileForm,
    SignupForm,
    password_form,
)
from accounts.middleware import local_auto_sign_in, onboarding_not_required
from accounts.services import (
    LOCAL_BACKEND,
    create_local_user,
    delete_account,
    profile_for,
    sign_in_without_password,
)


def safe_next(request) -> str:
    """The ``next`` parameter, if it points back into this site."""
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return ""


def waiting_counts(user) -> dict:
    """What an account already owns — shown to whoever inherits imported data."""
    from tracker.models import Application, Document

    return {
        "applications": Application.objects.filter(owner=user).count(),
        "documents": Document.objects.filter(owner=user).count(),
    }


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


@login_not_required
@onboarding_not_required
@require_http_methods(["GET", "POST"])
def onboarding(request):
    user = request.user if request.user.is_authenticated else None
    if user is None and conf.is_local() and local_auto_sign_in(request):
        user = request.user
    if user is not None and profile_for(user).is_onboarded:
        return redirect("tracker:dashboard")
    if user is None and not conf.is_local():
        return redirect("accounts:signup")

    form = OnboardingForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if user is None:
            user = create_local_user(form.cleaned_data["display_name"])
            sign_in_without_password(request, user)
        profile = form.apply(user)
        messages.success(request, f"Bienvenue, {profile.display_name}. Le suivi est à toi.")
        return redirect("tracker:dashboard")

    return render(
        request,
        "accounts/onboarding.html",
        {
            "form": form,
            "waiting": waiting_counts(user) if user is not None else None,
            "account": user,
        },
    )


@login_not_required
@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect(safe_next(request) or settings.LOGIN_REDIRECT_URL)
    if conf.is_local():
        return _local_chooser(request)

    form = LoginForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        auth.login(request, form.get_user())
        return redirect(safe_next(request) or settings.LOGIN_REDIRECT_URL)
    return render(
        request,
        "accounts/login.html",
        {"form": form, "next": safe_next(request), "signup_open": conf.signup_open()},
    )


def _local_chooser(request):
    """Local mode: pick a profile, no password. One profile signs in by itself."""
    User = get_user_model()
    users = User.objects.filter(is_active=True).select_related("profile").order_by("pk")
    if not users.exists():
        return redirect("accounts:onboarding")
    if users.count() == 1:
        sign_in_without_password(request, users.first())
        return redirect(safe_next(request) or settings.LOGIN_REDIRECT_URL)

    if request.method == "POST":
        raw = request.POST.get("user", "")
        user = users.filter(pk=raw).first() if raw.isdigit() else None
        if user is None:
            return HttpResponseBadRequest("Profil inconnu.")
        sign_in_without_password(request, user)
        return redirect(safe_next(request) or settings.LOGIN_REDIRECT_URL)

    from tracker.models import Application

    entries = []
    for user in users:
        profile = profile_for(user)
        entries.append(
            {
                "user": user,
                "profile": profile,
                "applications": Application.objects.filter(owner=user).count(),
            }
        )
    return render(request, "accounts/chooser.html", {"entries": entries, "next": safe_next(request)})


@login_not_required
@require_http_methods(["GET", "POST"])
def signup(request):
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    if conf.is_local():
        return redirect("accounts:onboarding")
    if not conf.signup_open():
        return render(request, "accounts/signup_closed.html", status=403)

    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        auth.login(request, user, backend=LOCAL_BACKEND)
        messages.success(request, f"Bienvenue, {request.profile.display_name}. Le suivi est à toi.")
        return redirect(settings.LOGIN_REDIRECT_URL)
    return render(request, "accounts/signup.html", {"form": form})


@login_not_required
@onboarding_not_required
@require_POST
def logout_view(request):
    auth.logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

SECTIONS = ("profil", "preferences", "mot-de-passe", "supprimer")


@require_http_methods(["GET", "POST"])
def settings_view(request, section: str | None = None):
    if section is not None and section not in SECTIONS:
        raise Http404
    if request.method == "GET" and section is not None:
        return redirect("accounts:settings")

    profile = request.profile
    preferences = request.preferences
    forms = {
        "profile": ProfileForm(instance=profile),
        "preferences": PreferencesForm(instance=preferences),
        "password": password_form(request.user),
        "delete": DeleteAccountForm(expected=profile.display_name),
    }

    if request.method == "POST":
        section = section or "profil"
        if section == "profil":
            form = forms["profile"] = ProfileForm(request.POST, instance=profile)
            if form.is_valid():
                form.save()
                messages.success(request, "Profil enregistré.")
                return redirect("accounts:settings")
        elif section == "preferences":
            form = forms["preferences"] = PreferencesForm(request.POST, instance=preferences)
            if form.is_valid():
                form.save()
                messages.success(request, "Préférences enregistrées.")
                return redirect("accounts:settings")
        elif section == "mot-de-passe":
            form = forms["password"] = password_form(request.user, request.POST)
            if form.is_valid():
                form.save()
                update_session_auth_hash(request, form.user)
                messages.success(request, "Mot de passe enregistré.")
                return redirect("accounts:settings")
        elif section == "supprimer":
            form = forms["delete"] = DeleteAccountForm(
                request.POST, expected=profile.display_name
            )
            if form.is_valid():
                delete_account(request, request.user)
                return redirect(reverse("accounts:login"))

    return render(
        request,
        "accounts/settings.html",
        {
            "page": "settings",
            "profile_form": forms["profile"],
            "preferences_form": forms["preferences"],
            "password_form": forms["password"],
            "delete_form": forms["delete"],
            "has_password": request.user.has_usable_password(),
            "is_local": conf.is_local(),
        },
    )
