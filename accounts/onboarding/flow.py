"""The screens of the questionnaire: the one table the machine runs on.

Chain order is table order. A guarded step only applies in some situations;
the guards are written *optimistic* on a missing answer (the cities count
until the work mode says remote) so that the ``n/N`` counter only ever
shrinks during a run. Labels are French, values English; the durable
choices are the ``TextChoices`` of ``accounts.models`` so the review, the
admin and the settings speak the same words.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.urls import reverse

from accounts.models import (
    AiToolsUsed,
    Challenge,
    EducationLevel,
    EmploymentStatus,
    ExperienceLevel,
    HelpWanted,
    SalaryPeriod,
    StartTimeline,
    WorkType,
)
from accounts.onboarding.machine import Answers, Context, Kind, Machine, Step
from tracker.models import WorkMode

# ---------------------------------------------------------------------------
# Guards: pure functions of the answers so far and the context
# ---------------------------------------------------------------------------


def has_waiting(answers: Answers, ctx: Context) -> bool:
    """The signed-in account already owns applications (the ownership migration's placeholder)."""
    return ctx.waiting_applications > 0


def ai_plugin(answers: Answers, ctx: Context) -> bool:
    return ctx.ai_plugin


def wants_cities(answers: Answers, ctx: Context) -> bool:
    """Optimistic while unanswered: a remote worker is the only one not asked a city."""
    return answers.get("work_mode") != WorkMode.REMOTE


def needs_identity(answers: Answers, ctx: Context) -> bool:
    return not ctx.authenticated or not ctx.display_name_known


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    description: str = ""
    #: A UI-only option that selects every other one ("Tout ça", "Peu importe").
    check_all: bool = False


def _choices(
    choices: Sequence[tuple[str, Any]], descriptions: dict[str, str] | None = None
) -> tuple[Option, ...]:
    """Model choices as options; the labels are plain strings, not lazy ones."""
    return tuple(Option(value, str(label), (descriptions or {}).get(value, "")) for value, label in choices)


#: The value of the "everything" option of the help step: never stored.
EVERYTHING = "everything"

OPTIONS: dict[str, tuple[Option, ...]] = {
    "status": _choices(EmploymentStatus.choices),
    "ai_tools": _choices(AiToolsUsed.choices),
    "challenge": _choices(Challenge.choices),
    "help": _choices(HelpWanted.choices) + (Option(EVERYTHING, "Tout ça", check_all=True),),
    "experience": _choices(
        ExperienceLevel.choices,
        {
            ExperienceLevel.ENTRY: "Moins de 2 ans, ou en reconversion",
            ExperienceLevel.MID: "2 à 5 ans",
            ExperienceLevel.SENIOR: "Plus de 5 ans",
            ExperienceLevel.LEAD: "J'encadre une équipe ou un service",
            ExperienceLevel.UNSURE: "On laisse ça de côté pour l'instant",
        },
    ),
    "education": _choices(EducationLevel.choices),
    "work_type": tuple(
        Option(value, str(label), check_all=value == WorkType.ANY) for value, label in WorkType.choices
    ),
    "work_mode": _choices([(value, label) for value, label in WorkMode.choices if value != WorkMode.UNKNOWN]),
    "timeline": _choices(StartTimeline.choices),
}

#: Search radius proposed on the cities step, in kilometres.
RADIUS_CHOICES: tuple[int, ...] = (10, 20, 40, 60, 100, 150)

#: Per period: (ceiling accepted by the form, slider minimum, slider maximum, slider step, unit label).
SALARY_BOUNDS: dict[str, tuple[int, int, int, int, str]] = {
    SalaryPeriod.HOUR: (500, 10, 100, 1, "€ brut / h"),
    SalaryPeriod.MONTH: (50_000, 1_500, 10_000, 50, "€ brut / mois"),
    SalaryPeriod.YEAR: (1_000_000, 20_000, 120_000, 500, "€ brut / an"),
}

def salary_periods() -> list[dict[str, Any]]:
    """The period toggle, one row per period: label, ceiling, slider window, unit (templates and fields.js)."""
    labels = dict(SalaryPeriod.choices)
    return [
        {"value": key, "label": str(labels[key]), "ceiling": ceiling, "min": lo, "max": hi, "step": st, "unit": unit}
        for key, (ceiling, lo, hi, st, unit) in SALARY_BOUNDS.items()
    ]


def salary_unit(period: str) -> str:
    """« € brut / mois » for the period in place — the month's when there is none yet."""
    return SALARY_BOUNDS.get(period, SALARY_BOUNDS[SalaryPeriod.MONTH])[4]


#: The steps the review offers to edit. The context answers (situation, AI
#: tools, difficulty, expectations) are reachable by Back, not edited here.
EDITABLE: frozenset[str] = frozenset(
    {"titles", "industries", "experience", "education", "work_type", "work_mode", "cities", "salary", "timeline"}
)

# ---------------------------------------------------------------------------
# The steps
# ---------------------------------------------------------------------------

_T = "accounts/onboarding/"

STEPS: tuple[Step, ...] = (
    Step(
        "welcome_back", "reprise", Kind.INTERSTITIAL, "status",
        guard=has_waiting, counted=False, template=_T + "welcome_back.html",
    ),
    Step(
        "status", "situation", Kind.SINGLE, "intro_pace",
        title="Où en es-tu aujourd'hui ?",
        lede="Ça règle le rythme des relances et ce qu'on te montre en premier.",
        answer_keys=("employment_status",),
    ),
    Step(
        "intro_pace", "rythme", Kind.INTERSTITIAL, "ai_tools",
        title="On avance à ton rythme.", template=_T + "interstitials/rythme.html",
    ),
    Step(
        "ai_tools", "outils-ia", Kind.SINGLE, "intro_copilot",
        title="As-tu déjà utilisé des outils d'IA pour chercher un emploi ?",
        answer_keys=("ai_tools_used",),
    ),
    Step(
        "intro_copilot", "copilote", Kind.INTERSTITIAL, "challenge",
        title="Ce que le copilote sait faire.", guard=ai_plugin,
        template=_T + "interstitials/copilote.html",
    ),
    Step(
        "challenge", "difficulte", Kind.SINGLE, "intro_profile",
        title="Qu'est-ce qui te pèse le plus dans ta recherche ?",
        answer_keys=("challenge",),
    ),
    Step(
        "intro_profile", "profil", Kind.INTERSTITIAL, "help",
        title="Renseigne ton profil une fois. Il te suit partout.",
        template=_T + "interstitials/profil.html",
    ),
    Step(
        "help", "aide", Kind.MULTI, "titles",
        title="Qu'attends-tu de JobHunt ?", lede="Plusieurs réponses possibles.",
        answer_keys=("help_wanted",),
    ),
    Step(
        "titles", "postes", Kind.CHIPS, "industries",
        title="Quels postes vises-tu ?",
        note="Un intitulé précis donne de meilleurs résultats : « Chef de projet » plutôt que « Manager ».",
        answer_keys=("job_titles",),
    ),
    Step(
        "industries", "secteurs", Kind.GRID, "experience",
        title="Dans quels secteurs ?", lede="Choisis-en autant que tu veux, ou passe.",
        answer_keys=("industries", "any_industry"),
        skip_values={"industries": [], "any_industry": False},
    ),
    Step(
        "experience", "experience", Kind.SINGLE, "education",
        title="Ton niveau d'expérience ?", answer_keys=("experience_level",), auto_advance=True,
    ),
    Step(
        "education", "formation", Kind.SINGLE, "work_type",
        title="Ton plus haut niveau de formation ?", answer_keys=("education_level",), auto_advance=True,
    ),
    Step(
        "work_type", "contrat", Kind.MULTI, "work_mode",
        title="Quel type de contrat ?", lede="Plusieurs réponses possibles.",
        answer_keys=("work_types",),
    ),
    Step(
        "work_mode", "mode", Kind.SINGLE, "cities",
        title="Sur site, hybride ou à distance ?",
        note="Ça décide quelles offres remontent en premier — et si on te demande une ville.",
        answer_keys=("work_mode",),
    ),
    Step(
        "cities", "villes", Kind.CITIES, "salary",
        title="Où irais-tu travailler sur place ?",
        note="La distance des offres se compte depuis la première ville. Modifiable dans les réglages.",
        guard=wants_cities, answer_keys=("cities", "search_radius_km"),
        skip_values={"cities": [], "search_radius_km": None},
    ),
    Step(
        "salary", "salaire", Kind.SALARY, "timeline",
        title="Ton salaire minimum ?", lede="Brut. Ça reste entre toi et ton tableau de bord.",
        note="Sert de repère quand tu compares des offres — et à rien d'autre.",
        answer_keys=("salary_min", "salary_period"),
        skip_values={"salary_min": None, "salary_period": ""},
    ),
    Step(
        "timeline", "horizon", Kind.SINGLE, "review",
        title="Quand veux-tu commencer ?", answer_keys=("start_timeline",),
    ),
    Step(
        "review", "bilan", Kind.REVIEW, "identity",
        title="Presque fini ! Relis tes réponses.",
        lede="Tu pourras tout changer plus tard dans les réglages.",
    ),
    Step(
        "identity", "identite", Kind.GATE, "cv",
        guard=needs_identity, answer_keys=("display_name",),
    ),
    Step(
        "cv", "cv", Kind.FILE, "plan",
        title="Ton CV, pour partir du bon pied",
        lede="Il devient ton CV de base dans Documents : chaque candidature part de là.",
        note="PDF ou DOCX, 20 Mo max. Le fichier reste dans ton espace ; seule une version "
        "anonymisée du texte peut être lue par une extension.",
        answer_keys=("cv_document_id", "cv_text_chars", "cv_redactions", "cv_analyzed", "cv_language"),
        skip_values={
            "cv_document_id": None, "cv_text_chars": None, "cv_redactions": "",
            "cv_analyzed": False, "cv_language": "",
        },
    ),
    Step("plan", "plan", Kind.PLAN, "done", title="Commence aujourd'hui."),
    Step("done", "", Kind.TERMINAL, None, counted=False),
)

#: The one machine the views run; ``check()`` runs here, at import.
machine = Machine(STEPS, initial="welcome_back", review="review", terminal="done")


def step_url(step_id: str) -> str:
    return reverse("accounts:onboarding_step", args=[machine.by_id[step_id].slug])
