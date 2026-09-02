"""The read model of the pages: lists and breakdowns, straight from the ORM.

Owned by the web adapter, not by the use cases: no rule lives here, only
the shapes each page needs, so there is no in-memory twin. Every function
returns lists or dicts (fully evaluated — nothing is paginated), keeps the
``select_related``/``prefetch_related`` that make the templates cheap, and
pins ``nulls_last`` on every nullable sort key so SQLite and PostgreSQL
agree on the order.
"""

from __future__ import annotations

import datetime as dt
from collections import OrderedDict

from django.db.models import Count, F, Q

from tracker.models import (
    CLOSED_STATUSES,
    IN_FLIGHT_STATUSES,
    OPEN_STATUSES,
    ActivityEvent,
    Application,
    Company,
    Document,
    Language,
    Platform,
    Sector,
    SkillGap,
    Status,
)


def company_names(user) -> list[str]:
    """For the datalist of the company field."""
    return list(Company.objects.for_user(user).order_by("name").values_list("name", flat=True))


def filtered_applications(user, data: dict) -> list[Application]:
    """The applications table, filtered and sorted by the filter form's
    cleaned data (an empty dict means the defaults)."""
    queryset = Application.objects.for_user(user).with_related()

    scope = data.get("scope") or "active"
    if scope == "active":
        queryset = queryset.filter(status__in=OPEN_STATUSES)
    elif scope == "closed":
        queryset = queryset.filter(status__in=CLOSED_STATUSES).exclude(status=Status.DISCARDED)
    elif scope == "discarded":
        queryset = queryset.filter(status=Status.DISCARDED)

    if statuses := data.get("status"):
        queryset = queryset.filter(status__in=statuses)
    if sector := data.get("sector"):
        queryset = queryset.filter(company__sector=sector)
    if language := data.get("language"):
        queryset = queryset.filter(cv_language=language)
    if work_mode := data.get("work_mode"):
        queryset = queryset.filter(work_mode=work_mode)
    if q := (data.get("q") or "").strip():
        queryset = queryset.filter(
            Q(company__name__icontains=q)
            | Q(title__icontains=q)
            | Q(location__icontains=q)
            | Q(summary__icontains=q)
            | Q(personal_notes__icontains=q)
            | Q(strengths__icontains=q)
            | Q(weaknesses__icontains=q)
            | Q(discard_reason__icontains=q)
        )

    sort = data.get("sort") or "-score"
    if sort == "pipeline":
        queryset = queryset.by_pipeline_order()
    elif sort == "-score":
        queryset = queryset.order_by(F("score").desc(nulls_last=True), "company__name")
    elif sort == "-applied_on":
        queryset = queryset.order_by(
            F("applied_on").desc(nulls_last=True), F("score").desc(nulls_last=True)
        )
    elif sort == "follow_up_on":
        queryset = queryset.order_by(
            F("follow_up_on").asc(nulls_last=True), F("score").desc(nulls_last=True)
        )
    else:
        queryset = queryset.order_by(sort, "company__name")

    return list(queryset)


def dashboard_lists(user, *, today: dt.date) -> dict:
    """The panels of the dashboard that are plain lists."""
    base = Application.objects.for_user(user).with_related()
    events = ActivityEvent.objects.filter(application__owner=user).select_related(
        "application", "application__company"
    )
    return {
        "to_apply": list(
            base.filter(status=Status.TO_APPLY).order_by(
                F("score").desc(nulls_last=True),
                F("distance_km").asc(nulls_last=True),
                "company__name",
            )
        ),
        "in_flight": list(
            base.filter(status__in=IN_FLIGHT_STATUSES).order_by(
                F("applied_on").asc(nulls_last=True), F("score").desc(nulls_last=True)
            )
        ),
        "backlog": list(
            base.filter(status=Status.BACKLOG).order_by(
                F("score").desc(nulls_last=True), "company__name"
            )
        ),
        "upcoming": list(events.filter(happened_on__gt=today).order_by("happened_on")[:6]),
        "recent": list(
            events.filter(happened_on__lte=today)
            .exclude(application__status=Status.DISCARDED)
            .order_by("-happened_on", "-created_at")[:8]
        ),
    }


def closed(user) -> list[Application]:
    """Closed files (not the ones written off at triage), most recent first."""
    return list(
        Application.objects.for_user(user)
        .with_related()
        .filter(status__in=CLOSED_STATUSES)
        .exclude(status=Status.DISCARDED)
        .order_by(F("closed_on").desc(nulls_last=True))
    )


def insights(user, radius: int) -> dict:
    """Breakdowns, score bands and the score/distance scatter of the analysis page."""
    applications = Application.objects.for_user(user)
    gaps = list(SkillGap.objects.for_user(user))
    platforms = list(Platform.objects.for_user(user).filter(is_lead=False))
    leads = list(Platform.objects.for_user(user).filter(is_lead=True))
    discarded = list(applications.with_related().discarded().order_by("company__name"))

    by_sector = [
        {
            "label": Sector(row["company__sector"]).label,
            "value": row["n"],
            "key": row["company__sector"],
        }
        for row in applications.active()
        .values("company__sector")
        .annotate(n=Count("id"))
        .order_by("-n")
    ]
    by_language = [
        {"label": Language(row["cv_language"]).label, "value": row["n"]}
        for row in applications.active()
        .values("cv_language")
        .annotate(n=Count("id"))
        .order_by("-n")
    ]
    scored = applications.active().exclude(score__isnull=True)
    bands = [
        {"label": "80 % et plus", "value": scored.filter(score__gte=80).count(), "tone": "green"},
        {
            "label": "65 – 79 %",
            "value": scored.filter(score__gte=65, score__lt=80).count(),
            "tone": "amber",
        },
        {"label": "moins de 65 %", "value": scored.filter(score__lt=65).count(), "tone": "red"},
    ]
    # The trade-off the dossier actually optimises: match quality against commute.
    plotted = [
        a
        for a in applications.with_related().active()
        if a.score is not None and a.distance_km is not None
    ]
    max_distance = max((a.distance_km for a in plotted), default=0)
    axis_max = max(radius, ((max_distance // 10) + 1) * 10) if plotted else radius
    points = [
        {
            "application": a,
            # Formatted here, not in the template: under a French locale Django
            # renders 75.0 as "75,0", which is not a valid CSS length.
            "x": f"{a.distance_km / axis_max * 100:.2f}",
            "y": a.score,
            "tone": {"high": "green", "mid": "amber", "low": "red"}.get(a.score_band, "slate"),
        }
        for a in plotted
    ]

    return {
        "gaps": gaps,
        "platforms": platforms,
        "leads": leads,
        "discarded": discarded,
        "by_sector": by_sector,
        "by_language": by_language,
        "bands": bands,
        "max_sector": max((row["value"] for row in by_sector), default=1) or 1,
        "max_language": max((row["value"] for row in by_language), default=1) or 1,
        "max_band": max((row["value"] for row in bands), default=1) or 1,
        "max_gap": max((gap.demand_count for gap in gaps), default=1) or 1,
        "points": points,
        "axis_max": axis_max,
    }


def document_library(user) -> dict:
    """Library documents on one side, attached ones grouped by application."""
    documents = Document.objects.for_user(user)
    library = list(documents.filter(application__isnull=True).order_by("kind", "label"))
    attached = list(
        documents.filter(application__isnull=False)
        .select_related("application", "application__company")
        .order_by("application__company__name", "kind")
    )
    grouped: OrderedDict[Application, list[Document]] = OrderedDict()
    for document in attached:
        grouped.setdefault(document.application, []).append(document)
    return {"library": library, "grouped": grouped, "total": len(library) + len(attached)}
