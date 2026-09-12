"""Template context shared by every page: navigation and its live counters."""

from __future__ import annotations

from importlib import import_module

from django.apps import apps
from django.urls import reverse
from django.utils import timezone

from accounts.services import preferences_for
from tracker import services

NAV_ITEMS = [
    ("tracker:dashboard", "Tableau de bord", "gauge", "attention"),
    ("tracker:pipeline", "Pipeline", "columns", "open"),
    ("tracker:application_list", "Candidatures", "rows", "tracked"),
    ("tracker:document_library", "Documents", "file", "documents"),
    ("tracker:insights", "Analyse", "chart", None),
]


def _copilot_hooks():
    """The copilot's chrome (nav entry, badges), or None when it is off.

    Imported on demand rather than at module level: ``jobhunt_ai`` is only
    importable when it is installed (``COPILOT_ENABLED``), and the core must
    render without it.
    """
    return import_module("jobhunt_ai.hooks") if apps.is_installed("jobhunt_ai") else None


def navigation(request):
    today = timezone.localdate()
    hooks = _copilot_hooks()
    context = {
        "today": today,
        "nav_items": [],
        "nav_counters": {},
        "copilot_enabled": hooks is not None,
    }
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        # Onboarding, sign-in: no rail, nothing to count.
        return context

    preferences = getattr(request, "preferences", None) or preferences_for(user)
    counters = services.nav_counters(
        user, stale_days=preferences.stale_after_days, today=today
    )
    if hooks is not None:
        counters.update(hooks.nav_badges(request))

    items = []
    for route, label, icon, counter in NAV_ITEMS + ([hooks.NAV_ITEM] if hooks else []):
        url = reverse(route)
        items.append(
            {
                "url": url,
                "label": label,
                "icon": icon,
                "count": counters.get(counter) if counter else None,
                "is_active": request.path == url
                or (url != "/" and request.path.startswith(url)),
            }
        )
    context.update({"nav_items": items, "nav_counters": counters})
    return context
