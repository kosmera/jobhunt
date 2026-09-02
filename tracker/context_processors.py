"""Template context shared by every page: navigation and its live counters."""

from __future__ import annotations

from django.urls import reverse
from django.utils import timezone

from accounts.services import preferences_for
from jobhunt.plugins import plugin_nav_badges, plugin_nav_items, plugin_templates
from tracker import services

NAV_ITEMS = [
    ("tracker:dashboard", "Tableau de bord", "gauge", "attention"),
    ("tracker:pipeline", "Pipeline", "columns", "open"),
    ("tracker:application_list", "Candidatures", "rows", "tracked"),
    ("tracker:document_library", "Documents", "file", "documents"),
    ("tracker:insights", "Analyse", "chart", None),
]


def navigation(request):
    today = timezone.localdate()
    context = {
        "today": today,
        "nav_items": [],
        "nav_counters": {},
        "plugin_icon_templates": plugin_templates("icon_templates"),
        "plugin_application_panels": plugin_templates("application_panels"),
    }
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        # Onboarding, sign-in: no rail, nothing to count.
        return context

    preferences = getattr(request, "preferences", None) or preferences_for(user)
    counters = services.nav_counters(
        user, stale_days=preferences.stale_after_days, today=today
    )
    counters.update(plugin_nav_badges(request))

    items = []
    for route, label, icon, counter in NAV_ITEMS + plugin_nav_items():
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
