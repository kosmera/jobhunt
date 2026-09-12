"""Gatekeeping: who may see what, and where a newcomer is sent first.

Builds on Django's ``LoginRequiredMiddleware`` (views opt out with
``login_not_required``) and adds four things:

- **homepage** — signed-out visitors land on the public page in both modes;
- **local mode** — entering a private page with no account yet sends the
  visitor to onboarding; with exactly one account it is signed in on the
  spot, with several the picker is shown;
- **onboarding** — an authenticated account without a completed profile is
  sent to ``/bienvenue/`` (views opt out with ``onboarding_not_required``);
- **HTMX** — a fragment request never receives a login page to swap in: it
  gets ``204`` plus ``HX-Redirect``. After an automatic sign-in the view still
  runs (the action is not lost) and the response carries ``HX-Refresh``,
  because signing in rotated the CSRF token the open page holds.

``request.profile`` / ``request.preferences`` are set lazily for every
request so templates and views read them without a query per access.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.middleware import LoginRequiredMiddleware
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import resolve_url
from django.urls import reverse
from django.utils.functional import SimpleLazyObject
from django.utils.http import url_has_allowed_host_and_scheme

from accounts import conf
from accounts.services import (
    ensure_local_admin, preferences_for, profile_for, sign_in_without_password,
)


def onboarding_not_required(view_func):
    """Mark a view reachable by an account that has not completed onboarding."""
    view_func.onboarding_required = False
    return view_func


def is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def local_auto_sign_in(request) -> bool:
    """Local mode: sign the single existing account in. True if it happened."""
    User = get_user_model()
    users = list(User.objects.filter(is_active=True).order_by("pk")[:2])
    if len(users) != 1:
        return False
    sign_in_without_password(request, users[0])
    return True


def _lazy_profile(request):
    return profile_for(request.user) if request.user.is_authenticated else None


def _lazy_preferences(request):
    return preferences_for(request.user) if request.user.is_authenticated else None


class AccountsMiddleware(LoginRequiredMiddleware):
    def process_request(self, request):
        ensure_local_admin(request.user)
        request.profile = SimpleLazyObject(lambda: _lazy_profile(request))
        request.preferences = SimpleLazyObject(lambda: _lazy_preferences(request))

    def process_view(self, request, view_func, view_args, view_kwargs):
        # Django's admin login opts out of the account gate. In local mode,
        # use the same passwordless entry/chooser as the rest of the app.
        match = request.resolver_match
        if (
            conf.is_local() and not request.user.is_authenticated
            and match and match.app_name == "admin" and match.url_name == "login"
        ):
            response = self._local_entry(request, view_func)
            if response is not None:
                return response
        if not getattr(view_func, "login_required", True):
            return None

        if not request.user.is_authenticated:
            # The homepage is the public entry point for signed-out visitors.
            if match and match.view_name == "tracker:dashboard":
                return self._redirect(request, reverse("landing"))
            if not conf.is_local():
                return self.handle_no_permission(request, view_func)
            response = self._local_entry(request, view_func)
            if response is not None:
                return response

        # ``profile`` is set on the request by ``process_request`` above.
        profile = getattr(request, "profile")
        if getattr(view_func, "onboarding_required", True) and not profile.is_onboarded:
            return self._redirect(request, reverse("accounts:onboarding"))
        return None

    # -- local mode ----------------------------------------------------------

    def _local_entry(self, request, view_func):
        User = get_user_model()
        count = User.objects.filter(is_active=True).count()
        if count == 0:
            return self._redirect(request, reverse("accounts:onboarding"))
        if count > 1:
            # Several profiles: the login page is the picker.
            return self.handle_no_permission(request, view_func)
        local_auto_sign_in(request)
        if is_htmx(request):
            # ``login()`` rotated the CSRF token the open page still carries:
            # let the action go through, then reload the page.
            request.accounts_refresh_page = True
        return None

    def process_response(self, request, response):
        if getattr(request, "accounts_refresh_page", False):
            response.headers["HX-Refresh"] = "true"
        return response

    # -- redirects ---------------------------------------------------------

    # An HTMX fragment gets a 204 with ``HX-Redirect``, not the redirect
    # response the base class promises — hence the wider return type.
    def handle_no_permission(self, request, view_func):  # pyright: ignore[reportIncompatibleMethodOverride]
        if not is_htmx(request):
            return super().handle_no_permission(request, view_func)
        login_url = resolve_url(self.get_login_url(view_func))
        next_url = self._htmx_next(request)
        if next_url:
            login_url = redirect_to_login(
                next_url, login_url, self.get_redirect_field_name(view_func)
            ).url
        return self._redirect(request, login_url)

    @staticmethod
    def _htmx_next(request) -> str:
        """The page the fragment was requested from, if it is one of ours."""
        current = request.headers.get("HX-Current-URL", "")
        if not current or not url_has_allowed_host_and_scheme(
            current, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return ""
        parts = urlsplit(current)
        return urlunsplit(("", "", parts.path, parts.query, ""))

    @staticmethod
    def _redirect(request, url: str):
        if is_htmx(request):
            response = HttpResponse(status=204)
            response.headers["HX-Redirect"] = url
            return response
        return HttpResponseRedirect(url)
