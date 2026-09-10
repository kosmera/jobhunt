"""What the screens need computed: the context, the CV intake, the review rows, the plan."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from accounts import conf
from accounts.models import (
    EducationLevel,
    ExperienceLevel,
    Industry,
    SalaryPeriod,
    StartTimeline,
    WorkType,
    weekly_cost,
)
from accounts.onboarding import data
from accounts.onboarding.flow import machine
from accounts.onboarding.machine import Answers, Context
from accounts.services import profile_for
from tracker import privacy
from tracker import services as tracker_services
from tracker.adapters import cv_analyzer
from tracker.models import Document, DocumentKind, WorkMode

# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


def waiting_counts(user) -> dict[str, int]:
    """What an account already owns — shown to whoever inherits imported data."""
    from tracker.models import Application

    return {
        "applications": Application.objects.filter(owner=user).count(),
        "documents": Document.objects.filter(owner=user).count(),
    }


def build_context(user) -> Context:
    """What the guards read: nothing that the answers already hold."""
    if user is None:
        return Context(ai_plugin=cv_analyzer() is not None)
    return Context(
        authenticated=True,
        display_name_known=bool(profile_for(user).display_name.strip()),
        ai_plugin=cv_analyzer() is not None,
        waiting_applications=waiting_counts(user)["applications"],
    )


def gate_variant(user, ctx: Context) -> str:
    """Which identity screen: a name for a local profile, a sign-up form, or a name for an account made elsewhere."""
    if user is None:
        return "local_new" if conf.is_local() else "accounts_signup"
    if ctx.waiting_applications > 0:
        return "local_claim"
    return "named_later"


# ---------------------------------------------------------------------------
# The CV step
# ---------------------------------------------------------------------------

#: ``Document.label`` is a 250-character column; an upload name may run to 255.
LABEL_MAX_LENGTH: int = getattr(Document._meta.get_field("label"), "max_length", None) or 250


def store_cv(request, *, upload, language: str, replacing: int | None) -> dict[str, Any]:
    """File the upload as the library's base CV; return the five ``cv_*`` answers.

    The same call ``tracker.views.add_document`` makes: the file goes through
    the storage port, the anonymised text (never the file) to the extension
    when one listens. A replacement demotes the previous onboarding upload —
    nothing is deleted from a first-run screen. The library's existing
    primary CV (imported data) keeps its rank.
    """
    user = request.user
    library = Document.objects.for_user(user).filter(kind=DocumentKind.CV, application__isnull=True)
    previous = library.filter(pk=replacing).first() if replacing else None
    is_primary = previous.is_primary if previous else not library.filter(is_primary=True).exists()
    if previous is not None and previous.is_primary:
        Document.objects.filter(pk=previous.pk).update(is_primary=False)
    document = Document(kind=DocumentKind.CV, is_primary=is_primary, language=language)
    profile = profile_for(user)
    intake = tracker_services.ingest_cv(
        user,
        None,
        document,
        upload=upload,
        label=(upload.name or "CV")[:LABEL_MAX_LENGTH],
        known=privacy.known_identity(
            display_name=profile.display_name,
            username=user.get_username(),
            email=user.email,
            location=profile.location,
            phone=profile.phone,
        ),
        analyzer=cv_analyzer(),
    )
    text = intake.anonymized.text.strip() if intake.anonymized is not None else None
    return {
        "cv_document_id": intake.document.pk,
        "cv_text_chars": len(text) if text is not None else None,
        "cv_redactions": intake.anonymized.summary() if intake.anonymized is not None else "",
        "cv_analyzed": intake.analyzed,
        "cv_language": language,
    }


@dataclass(frozen=True)
class CVCard:
    document: Document
    text_chars: int | None
    redactions: str
    #: ``analyzed`` · ``no_plugin`` · ``unreadable`` · ``too_short`` · ``not_analyzed``
    status: str


def cv_card(user, answers: Answers, ctx: Context) -> CVCard | None:
    """The result card, from the ``Document`` row — the truth for "a CV exists"."""
    document_id = answers.get("cv_document_id")
    if not isinstance(document_id, int) or isinstance(document_id, bool):
        return None
    document = (
        Document.objects.for_user(user)
        .filter(pk=document_id, kind=DocumentKind.CV, application__isnull=True)
        .first()
    )
    if document is None:
        return None
    text_chars = answers.get("cv_text_chars")
    if not isinstance(text_chars, int) or isinstance(text_chars, bool):
        text_chars = None
    if answers.get("cv_analyzed"):
        status = "analyzed"
    elif not ctx.ai_plugin:
        status = "no_plugin"
    elif not text_chars:
        status = "unreadable"
    elif text_chars < tracker_services.MIN_TEXT_LENGTH:
        status = "too_short"
    else:
        status = "not_analyzed"  # long enough, but the extension refused it
    redactions = answers.get("cv_redactions")
    return CVCard(document, text_chars, redactions if isinstance(redactions, str) else "", status)


# ---------------------------------------------------------------------------
# Review and plan
# ---------------------------------------------------------------------------

THIN_SPACE = " "
NO_BREAK_SPACE = " "


def format_euros(amount: int) -> str:
    """« 1 362 € », thousands separated by a narrow no-break space."""
    return f"{amount:,}".replace(",", THIN_SPACE) + NO_BREAK_SPACE + "€"


def salary_display(salary_min: Any, period: Any) -> str:
    """« 5 900 € brut par mois », or ``""`` without a salary."""
    if not isinstance(salary_min, int) or isinstance(salary_min, bool) or salary_min <= 0:
        return ""
    label = dict(SalaryPeriod.choices).get(period)
    if label is None:
        return ""
    return f"{format_euros(salary_min)} brut {str(label).lower()}"


@dataclass(frozen=True)
class ReviewRow:
    label: str
    value: str
    slug: str
    skipped: bool = False


SKIPPED = "— passé"


def _labels(values: Any, choices: Sequence[tuple[str, Any]]) -> str:
    lookup = {value: str(label) for value, label in choices}
    if not isinstance(values, list):
        return ""
    return ", ".join(lookup[v] for v in values if v in lookup)


def review_rows(answers: Answers, ctx: Context) -> list[ReviewRow]:
    """One row per preference, in flow order; a skipped step reads « — passé »."""
    rows: list[ReviewRow] = []
    titles = answers.get("job_titles")
    rows.append(ReviewRow("Postes visés", ", ".join(titles) if isinstance(titles, list) else "", "postes"))
    if answers.get("any_industry"):
        rows.append(ReviewRow("Secteurs", "Tous secteurs", "secteurs"))
    else:
        sectors = _labels(answers.get("industries"), Industry.choices)
        rows.append(ReviewRow("Secteurs", sectors or SKIPPED, "secteurs", skipped=not sectors))
    rows.append(ReviewRow("Expérience", _labels([answers.get("experience_level")], ExperienceLevel.choices), "experience"))
    rows.append(ReviewRow("Formation", _labels([answers.get("education_level")], EducationLevel.choices), "formation"))
    rows.append(ReviewRow("Contrat", _labels(answers.get("work_types"), WorkType.choices), "contrat"))
    rows.append(ReviewRow("Mode de travail", _labels([answers.get("work_mode")], WorkMode.choices), "mode"))
    if machine.enabled("cities", answers, ctx):
        cities = answers.get("cities")
        radius = answers.get("search_radius_km")
        if isinstance(cities, list) and cities:
            value = ", ".join(cities) + (f" · {radius} km" if isinstance(radius, int) else "")
            rows.append(ReviewRow("Villes et rayon", value, "villes"))
        else:
            rows.append(ReviewRow("Villes et rayon", SKIPPED, "villes", skipped=True))
    salary = salary_display(answers.get("salary_min"), answers.get("salary_period"))
    rows.append(ReviewRow("Salaire minimum", salary or SKIPPED, "salaire", skipped=not salary))
    rows.append(ReviewRow("Horizon", _labels([answers.get("start_timeline")], StartTimeline.choices), "horizon"))
    return rows


@dataclass(frozen=True)
class PlanRow:
    when: str
    text: str


def plan_rows(preferences) -> list[PlanRow]:
    return [
        PlanRow("Aujourd'hui", "Ajoute les offres que tu as déjà repérées : le pipeline commence par un vivier."),
        PlanRow(
            "Cette semaine",
            "Envoie ta première candidature depuis JobHunt : la relance se programme toute seule "
            f"à J+{preferences.follow_up_days}.",
        ),
        PlanRow(
            "Dans 15 jours",
            "Relances faites, entretiens dans la chronologie : regarde dans Analyse ce que le marché "
            "te demande le plus.",
        ),
    ]


def cost_line(answers: Answers) -> str:
    """The weekly cost at the visitor's own minimum salary, formatted; ``""`` when skipped."""
    salary = answers.get("salary_min")
    period = answers.get("salary_period")
    if not isinstance(salary, int) or isinstance(salary, bool) or not isinstance(period, str):
        return ""
    weekly = weekly_cost(salary, period)
    return format_euros(weekly) if weekly else ""


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


def related_titles(picked: Sequence[str]) -> list[str]:
    return data.related_titles(picked)


def industry_cards(selected: Mapping[str, Any] | Sequence[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The primary grid and the cards behind « Voir plus », with their labels and checked state."""
    labels = dict(Industry.choices)
    chosen = set(selected) if not isinstance(selected, Mapping) else set()

    def card(value: str) -> dict[str, Any]:
        return {"value": value, "label": str(labels[value]), "icon": f"sector-{value}", "checked": value in chosen}

    return [card(v) for v in data.INDUSTRIES_PRIMARY], [card(v) for v in data.INDUSTRIES_MORE]


def default_radius(user) -> int:
    if user is not None:
        from accounts.services import preferences_for

        return preferences_for(user).search_radius_km
    return settings.DEFAULT_SEARCH_RADIUS_KM
