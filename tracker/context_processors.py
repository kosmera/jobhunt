"""Template context shared by every page: navigation and its live counters."""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from tracker.models import (
    Application,
    Document,
    IN_FLIGHT_STATUSES,
    OPEN_STATUSES,
    Status,
)

NAV_ITEMS = [
    ("tracker:dashboard", "Tableau de bord", "gauge", "attention"),
    ("tracker:pipeline", "Pipeline", "columns", "open"),
    ("tracker:application_list", "Candidatures", "rows", "tracked"),
    ("tracker:document_library", "Documents", "file", "documents"),
    ("tracker:insights", "Analyse", "chart", None),
]


def navigation(request):
    today = timezone.localdate()
    rows = {
        row["status"]: row["n"]
        for row in Application.objects.values("status").annotate(n=Count("id"))
    }
    attention = Application.objects.filter(
        Q(follow_up_on__lte=today, status__in=IN_FLIGHT_STATUSES)
        | Q(
            status=Status.SENT,
            applied_on__lte=today - dt.timedelta(days=settings.STALE_AFTER_DAYS),
        )
    ).count()

    counters = {
        "open": sum(rows.get(s, 0) for s in OPEN_STATUSES),
        "tracked": sum(n for s, n in rows.items() if s != Status.DISCARDED),
        "attention": attention,
        "documents": Document.objects.count(),
    }

    items = []
    for route, label, icon, counter in NAV_ITEMS:
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
    return {"nav_items": items, "nav_counters": counters, "today": today}
