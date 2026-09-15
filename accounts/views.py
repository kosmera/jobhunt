"""Sign-in, sign-up, sign-out and the settings page. The questionnaire that
follows sign-up (and greets a local machine) lives in ``accounts.onboarding``."""

from __future__ import annotations

from typing import Any
from ipaddress import ip_address

from django.conf import settings
from django.contrib import auth, messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_not_required
from django.http import Http404, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.csrf import csrf_failure as django_csrf_failure
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from accounts import conf
from accounts.forms import (
    DeleteAccountForm,
    EmailLoginForm,
    LoginForm,
    PreferencesForm,
    ProfileForm,
    SearchProfileForm,
    SignupForm,
    password_form,
)
from accounts.middleware import is_htmx, onboarding_not_required
from accounts.onboarding import data as suggestions
from accounts.onboarding.flow import salary_periods, salary_unit
from accounts.services import (
    LOCAL_BACKEND,
    delete_account,
    profile_for,
    search_profile_or_blank,
    sign_in_without_password,
)
from rls import as_user


def safe_next(request) -> str:
    """The ``next`` parameter, if it points back into this site."""
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return ""


def email_client_ip(request):
    candidates = [request.META.get("REMOTE_ADDR", "")]
    if settings.TRUST_AZURE_CLIENT_IP:
        candidates.insert(0, request.META.get("HTTP_CLIENT_IP", ""))
    for candidate in candidates:
        try:
            return str(ip_address(candidate))
        except ValueError:
            # Azure may append a source port; bracketed IPv6 stays unambiguous.
            host, separator, port = candidate.rpartition(":")
            if separator and port.isdecimal() and (host.startswith("[") or host.count(":") == 0):
                try:
                    return str(ip_address(host.strip("[]")))
                except ValueError:
                    pass
    return "unknown"


def queue_email_link(request, email, **values):
    from accounts.magic_links import request_link

    pending = {"email": email, **values}
    user = pending.pop("user", None)
    if user is not None:
        pending["user_id"] = user.pk
    request.session["pending_email_link"] = pending
    request_link(email, ip=email_client_ip(request), **values)


@login_not_required
@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect(safe_next(request) or settings.LOGIN_REDIRECT_URL)
    if conf.is_local():
        return _local_chooser(request)

    if conf.passwordless():
        form = EmailLoginForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            queue_email_link(request, form.cleaned_data["email"], next_path=safe_next(request))
            return redirect("accounts:email_link_sent")
        return render(request, "accounts/login.html", {"form": form, "next": safe_next(request)})

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
        # Nobody is signed in yet: each profile is read on its own account's
        # behalf, the only way the database lets it through.
        with as_user(user):
            entries.append(
                {
                    "user": user,
                    "profile": profile_for(user),
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
    if conf.passwordless():
        # New visitors finish the questionnaire before providing an address.
        # Old bookmarks and stale signup forms follow the same entry point.
        return redirect("accounts:onboarding")

    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        auth.login(request, user, backend=LOCAL_BACKEND)
        # The account exists and is named; the questionnaire fills the rest and
        # opens the dashboard with the welcome flash.
        return redirect("accounts:onboarding")
    return render(request, "accounts/signup.html", {"form": form})


@login_not_required
@onboarding_not_required
@never_cache
@require_http_methods(["GET"])
def email_link_sent(request):
    pending = request.session.get("pending_email_link", {})
    return render(request, "accounts/email_link_sent.html", {
        "can_resend": bool(pending),
        "onboarding_url": reverse("accounts:onboarding") if pending.get("purpose") == "signup" else "",
    })


@login_not_required
@onboarding_not_required
@never_cache
@require_POST
def email_link_resend(request):
    pending = dict(request.session.get("pending_email_link", {}))
    if pending and conf.passwordless():
        owner_id = pending.pop("user_id", None)
        if owner_id is not None:
            if not request.user.is_authenticated or request.user.pk != owner_id:
                return redirect("accounts:login")
            pending["user"] = request.user
        queue_email_link(request, **pending)
    return redirect("accounts:email_link_sent")


@login_not_required
@onboarding_not_required
@never_cache
@require_http_methods(["GET", "POST"])
def email_link_confirm(request, token):
    from accounts.magic_links import consume_link, read_link
    from accounts.onboarding import store

    link = read_link(token)
    if request.method == "POST" and link is not None:
        # An address-change proof belongs to the already authenticated owner.
        # Ordinary sign-in/signup links can be used on another device.
        if link.purpose == "change" and (
            not request.user.is_authenticated or request.user.pk != link.user_id
        ):
            messages.info(request, "Connecte-toi avec ton adresse actuelle avant de confirmer ce changement.")
            return redirect(f"{reverse('accounts:login')}?next={reverse('accounts:settings')}")
        result = consume_link(token)
        if result is not None:
            user, link = result
            # Never carry a questionnaire belonging to a different account
            # across sign-in. Signup restores only the confirmed request's data.
            store.clear(request)
            request.session.pop("pending_email_link", None)
            auth.login(request, user, backend=LOCAL_BACKEND)
            if link.purpose == "signup" and link.onboarding_data and not profile_for(user).is_onboarded:
                request.session[store.SESSION_KEY] = link.onboarding_data
            if link.purpose == "change":
                messages.success(request, "Nouvelle adresse e-mail confirmée.")
                destination = reverse("accounts:settings")
            elif not profile_for(user).is_onboarded:
                destination = reverse("accounts:onboarding")
            else:
                destination = link.next_path
                if not url_has_allowed_host_and_scheme(
                    destination, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
                ):
                    destination = settings.LOGIN_REDIRECT_URL
            return redirect(destination or settings.LOGIN_REDIRECT_URL)
        link = None
    response = render(request, "accounts/email_link_confirm.html", {
        "valid": link is not None,
        "signup": link is not None and link.purpose == "signup",
        "email_change": link is not None and link.purpose == "change",
        "email": link.email if link is not None else "",
    }, status=200 if link is not None else 400)
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@login_not_required
@onboarding_not_required
@require_POST
def logout_view(request):
    auth.logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)


def csrf_failure(request, reason: str = ""):
    """``CSRF_FAILURE_VIEW``: a fragment request with a stale token reloads its page.

    A sign-in rotates the token; a tab opened before it still sends the old
    one and htmx swaps nothing on a 403. ``HX-Refresh`` makes that tab reload
    and read the new token from the page. Full pages get Django's own view.
    """
    if is_htmx(request):
        response = HttpResponseForbidden("Jeton CSRF périmé : la page va se recharger.")
        response.headers["HX-Refresh"] = "true"
        return response
    return django_csrf_failure(request, reason=reason)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

SECTIONS = ("profil", "recherche", "preferences", "mot-de-passe", "supprimer")


@require_http_methods(["GET", "POST"])
def settings_view(request, section: str | None = None):
    if section is not None and section not in SECTIONS:
        raise Http404
    if conf.passwordless() and (section == "mot-de-passe" or request.POST.get("section") == "mot-de-passe"):
        raise Http404
    if request.method == "GET" and section is not None:
        return redirect("accounts:settings")

    profile = request.profile
    preferences = request.preferences
    search = search_profile_or_blank(request.user)
    forms = {
        "profile": ProfileForm(instance=profile),
        "search": SearchProfileForm(instance=search),
        "preferences": PreferencesForm(instance=preferences),
        "password": None if conf.passwordless() else password_form(request.user),
        "delete": DeleteAccountForm(expected=profile.display_name),
    }

    if request.method == "POST":
        section = section or "profil"
        if section == "profil":
            form = forms["profile"] = ProfileForm(request.POST, instance=profile)
            if form.is_valid():
                form.save()
                if conf.passwordless() and form.cleaned_data["email"] != request.user.email:
                    queue_email_link(
                        request, form.cleaned_data["email"], purpose="change", user=request.user,
                    )
                    messages.info(request, "Confirme la nouvelle adresse avec le lien reçu par e-mail. Ton adresse actuelle reste active.")
                messages.success(request, "Profil enregistré.")
                return redirect("accounts:settings")
        elif section == "recherche":
            form = forms["search"] = SearchProfileForm(request.POST, instance=search)
            if form.is_valid():
                form.save()
                messages.success(request, "Recherche enregistrée.")
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
            "search_form": forms["search"],
            "search": _search_context(forms["search"]),
            "search_known": search.pk is not None,
            "preferences_form": forms["preferences"],
            "password_form": forms["password"],
            "delete_form": forms["delete"],
            "has_password": request.user.has_usable_password(),
            "is_local": conf.is_local(),
        },
    )


def _search_context(form: SearchProfileForm) -> dict[str, Any]:
    """What the chips and the salary toggle of the search section need, beyond the form."""
    return {
        "titles_suggestions": list(suggestions.JOB_TITLES),
        "recommended": suggestions.related_titles(form.titles),
        "cities_suggestions": [[name, province] for name, province in suggestions.CITIES],
        "periods": salary_periods(),
        "unit": salary_unit(form["salary_period"].value() or ""),
    }
