"""Account access, including the metered SaaS free tier; never provider keys."""

import functools

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse

from accounts.services import has_premium
from jobhunt_ai.quotas import QuotaExceededException
from jobhunt_ai.settings import get, is_saas_production

PREMIUM_REQUIRED = "Le copilote est inclus dans l'abonnement Premium."


def has_copilot_access(user) -> bool:
    if not user or not user.is_authenticated or not user.pk or not user.is_active:
        return False
    if is_saas_production():
        return True  # Freemium access is metered at the AI port, too, in workers.
    return has_premium(user)


def require_copilot_access(user) -> None:
    if not has_copilot_access(user):
        raise PermissionDenied(PREMIUM_REQUIRED)


def premium_required(view):
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        if not has_copilot_access(request.user):
            # Un fragment 4xx ne serait pas échangé par htmx : les boutons
            # resteraient muets. On renvoie plutôt vers la page Premium.
            if request.headers.get("HX-Request"):
                response = HttpResponse(status=204)
                response.headers["HX-Redirect"] = reverse("jobhunt_ai:copilot")
                return response
            return render(
                request, "jobhunt_ai/premium_required.html", {"page": "copilot"}, status=402
            )
        try:
            return view(request, *args, **kwargs)
        except QuotaExceededException as exc:
            if request.resolver_match and request.resolver_match.url_name.startswith("api_"):
                return JsonResponse({"error": exc.as_dict()}, status=429)
            # HTMX swaps 2xx fragments by default; a 429 body is ignored.
            return render(
                request, "jobhunt_ai/partials/quota_exceeded.html",
                {"quota": exc.as_dict(), "upgrade_url": get("UPGRADE_URL")},
                status=200 if request.headers.get("HX-Request") else 429,
            )

    return wrapped
