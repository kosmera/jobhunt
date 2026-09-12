"""Who is using the tracker, and how they like it to behave.

The stock ``auth.User`` stays the account: swapping ``AUTH_USER_MODEL`` after
``auth``/``admin`` migrations have run breaks every existing database. What
the user model does not carry lives in two one-to-one rows:

- ``Profile`` — identity shown in the interface (name, home base, phone). Its
  ``onboarded_at`` doubles as the "has been through onboarding" flag.
- ``Preferences`` — the knobs that used to be environment variables (follow-up
  delay, staleness threshold) plus the search radius and default CV language.
- ``SearchProfile`` — what the account is looking for, as answered by the
  onboarding questionnaire (``accounts.onboarding``): titles, sectors, work
  mode, salary floor, start horizon, and the context answers that shape the
  copy shown to the account.

The first two are created with the user (signal) and, defensively, on first
access (``services.profile_for`` / ``services.preferences_for``); the third
exists once onboarding (or ``services.search_profile_for``) wrote it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

# Cycle-free: ``tracker.models`` imports nothing from ``accounts`` at module
# level (the one ``accounts.services`` import sits inside a method).
from tracker.models import WorkMode


# The environment variables keep their meaning as *defaults for a new
# profile*; module-level callables so migrations serialise a reference, not
# whatever value the environment held when ``makemigrations`` ran.
def default_stale_after_days() -> int:
    return settings.STALE_AFTER_DAYS


def default_follow_up_days() -> int:
    return settings.DEFAULT_FOLLOW_UP_DAYS


def default_search_radius_km() -> int:
    return settings.DEFAULT_SEARCH_RADIUS_KM


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    display_name = models.CharField("nom affiché", max_length=120, blank=True)
    headline = models.CharField(
        "titre professionnel",
        max_length=200,
        blank=True,
        help_text="Ce que tu cherches, en une ligne : « Ingénieur DevOps senior ».",
    )
    location = models.CharField(
        "point de départ",
        max_length=200,
        blank=True,
        help_text="La ville d'où se comptent les distances des offres.",
    )
    phone = models.CharField(
        "téléphone",
        max_length=60,
        blank=True,
        help_text="Facultatif. Sert d'en-tête aux CV que le copilote rédige, "
        "et l'anonymisation le masque partout ailleurs.",
    )
    onboarded_at = models.DateTimeField("profil complété le", null=True, blank=True)
    premium_until = models.DateTimeField(
        "premium jusqu'au", null=True, blank=True,
        help_text="Fin de la période payée. Sans date ou après expiration, le compte est gratuit.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "profil"
        verbose_name_plural = "profils"

    def __str__(self) -> str:
        return self.display_name or self.user.get_username()

    @property
    def is_onboarded(self) -> bool:
        return self.onboarded_at is not None

    @property
    def is_premium(self) -> bool:
        """Whether the paid period is running, as read on this instance.

        Mirrors ``premium_until`` (the single source of truth, admin-managed)
        for templates and code that already hold the profile. Access
        decisions go through ``accounts.services.has_premium(user)``, which
        reads the database afresh and also requires an active account.
        """
        return self.premium_until is not None and self.premium_until > timezone.now()

    @property
    def initials(self) -> str:
        source = self.display_name.strip() or self.user.get_username()
        parts = [part for part in source.replace("-", " ").split() if part]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()


class Preferences(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="preferences"
    )
    stale_after_days = models.PositiveSmallIntegerField(
        "sans nouvelles après (jours)",
        default=default_stale_after_days,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text="Au-delà, une candidature envoyée remonte dans « À traiter maintenant ».",
    )
    follow_up_days = models.PositiveSmallIntegerField(
        "relance proposée à (jours)",
        default=default_follow_up_days,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text="Délai entre l'envoi d'une candidature et la relance programmée.",
    )
    search_radius_km = models.PositiveSmallIntegerField(
        "rayon de recherche (km)",
        default=default_search_radius_km,
        validators=[MinValueValidator(5), MaxValueValidator(300)],
        help_text="Autour de ton point de départ.",
    )
    default_cv_language = models.CharField(
        "langue de CV par défaut",
        max_length=2,
        choices=[("fr", "Français"), ("en", "Anglais"), ("nl", "Néerlandais")],
        default="fr",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "préférences"
        verbose_name_plural = "préférences"

    def __str__(self) -> str:
        return f"Préférences de {self.user.get_username()}"


# ---------------------------------------------------------------------------
# The search profile: what the account is looking for
# ---------------------------------------------------------------------------


class EmploymentStatus(models.TextChoices):
    UNEMPLOYED_URGENT = "unemployed_urgent", "Sans emploi, il me faut un poste rapidement"
    UNEMPLOYED_EXPLORING = "unemployed_exploring", "Sans emploi, je prends le temps d'explorer"
    EMPLOYED_READY = "employed_ready", "En poste, prêt·e à changer"
    EMPLOYED_OPEN = "employed_open", "En poste, à l'écoute du marché"


class AiToolsUsed(models.TextChoices):
    YES = "yes", "Oui"
    NO = "no", "Non"
    NOT_SURE = "not_sure", "Pas vraiment"


class Challenge(models.TextChoices):
    NO_REPLY = "no_reply", "Je postule, mais je n'ai presque jamais de réponse"
    TOO_SLOW = "too_slow", "Postuler me prend trop de temps"
    POOR_FIT = "poor_fit", "Difficile de trouver des offres qui me correspondent vraiment"
    LOST = "lost", "Je ne sais pas par où commencer"


class HelpWanted(models.TextChoices):
    TRACK = "track", "Suivre mes candidatures et mes relances"
    DOCUMENTS = "documents", "Garder mes CV et mes lettres au même endroit"
    FIT = "fit", "Savoir si une offre me correspond avant de postuler — copilote IA (Premium)"
    GAPS = "gaps", "Voir les compétences qu'on me demande le plus"


class Industry(models.TextChoices):
    IT = "it", "Informatique & logiciel"
    HEALTH = "health", "Santé & soins"
    FINANCE = "finance", "Finance & assurance"
    SALES = "sales", "Vente & développement commercial"
    MARKETING = "marketing", "Marketing & communication"
    RETAIL = "retail", "Commerce & distribution"
    EDUCATION = "education", "Enseignement & formation"
    HR = "hr", "RH & recrutement"
    HOSPITALITY = "hospitality", "Horeca & tourisme"
    MANUFACTURING = "manufacturing", "Industrie & métiers techniques"
    LOGISTICS = "logistics", "Logistique & transport"
    MEDIA = "media", "Médias & culture"
    CONSTRUCTION = "construction", "Architecture & construction"
    REAL_ESTATE = "real_estate", "Immobilier"
    PUBLIC = "public", "Secteur public & non-marchand"
    ENERGY = "energy", "Énergie & environnement"
    CONSULTING = "consulting", "Consultance"
    SCIENCE = "science", "Pharma, biotech & sciences"


class ExperienceLevel(models.TextChoices):
    ENTRY = "entry", "Débutant·e"
    MID = "mid", "Confirmé·e"
    SENIOR = "senior", "Senior"
    LEAD = "lead", "Direction"
    UNSURE = "unsure", "Je ne sais pas trop"


class EducationLevel(models.TextChoices):
    NONE = "none", "Sans diplôme particulier"
    SECONDARY = "secondary", "Secondaire (CESS)"
    VOCATIONAL = "vocational", "Formation professionnelle ou graduat"
    BACHELOR = "bachelor", "Bachelier"
    MASTER = "master", "Master ou plus"


class WorkType(models.TextChoices):
    PERMANENT = "permanent", "CDI"
    FIXED_TERM = "fixed_term", "CDD ou intérim"
    PART_TIME = "part_time", "Temps partiel"
    FREELANCE = "freelance", "Freelance ou mission"
    ANY = "any", "Peu importe"


class SalaryPeriod(models.TextChoices):
    HOUR = "hour", "Par heure"
    MONTH = "month", "Par mois"
    YEAR = "year", "Par an"


class StartTimeline(models.TextChoices):
    ASAP = "asap", "Dès que possible"
    ONE_TO_THREE_MONTHS = "one_to_three_months", "Dans les 1 à 3 mois"
    OPEN = "open", "Quand la bonne occasion se présente"


#: Hours of a legal full-time week in Belgium, for an hourly floor.
FULL_TIME_HOURS_PER_WEEK = 38
#: Upper bounds of the JSON lists; the forms enforce the same numbers.
MAX_JOB_TITLES = 10
MAX_CITIES = 5
MAX_LIST_ENTRY_LENGTH = 80


def weekly_cost(salary_min: int | None, period: str) -> int | None:
    """Gross euros per week at the minimum salary; ``None`` without a salary.

    A month is a twelfth of a year, a year has 52 weeks, an hour is paid 38
    times a week: the figure shown on the plan screen is the visitor's own
    number, nothing else.
    """
    if salary_min is None or salary_min <= 0:
        return None
    if period == SalaryPeriod.MONTH:
        return round(salary_min * 12 / 52)
    if period == SalaryPeriod.HOUR:
        return round(salary_min * FULL_TIME_HOURS_PER_WEEK)
    if period == SalaryPeriod.YEAR:
        return round(salary_min / 52)
    return None


class SearchProfile(models.Model):
    """What the account is looking for, as answered at onboarding.

    The lists are plain JSON arrays of choice values or short strings: a
    search profile is read as a whole and never queried by element, so a join
    table per list would be ceremony. Not created by the post_save signal: it
    exists once onboarding (or ``services.search_profile_for``) wrote it.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="search_profile"
    )
    employment_status = models.CharField(
        "situation", max_length=24, choices=EmploymentStatus.choices, blank=True
    )
    ai_tools_used = models.CharField(
        "outils d'IA déjà essayés", max_length=12, choices=AiToolsUsed.choices, blank=True
    )
    challenge = models.CharField(
        "principale difficulté", max_length=12, choices=Challenge.choices, blank=True
    )
    help_wanted = models.JSONField("attentes", default=list, blank=True)
    job_titles = models.JSONField("postes visés", default=list, blank=True)
    industries = models.JSONField("secteurs", default=list, blank=True)
    any_industry = models.BooleanField("tous secteurs", default=False)
    experience_level = models.CharField(
        "niveau d'expérience", max_length=8, choices=ExperienceLevel.choices, blank=True
    )
    education_level = models.CharField(
        "formation", max_length=12, choices=EducationLevel.choices, blank=True
    )
    work_types = models.JSONField("types de contrat", default=list, blank=True)
    work_mode = models.CharField(
        "mode de travail", max_length=10, choices=WorkMode.choices, default=WorkMode.UNKNOWN
    )
    cities = models.JSONField("villes", default=list, blank=True)
    salary_min = models.PositiveIntegerField("salaire minimum (EUR brut)", null=True, blank=True)
    salary_period = models.CharField(
        "période du salaire", max_length=6, choices=SalaryPeriod.choices, blank=True
    )
    start_timeline = models.CharField(
        "horizon de départ", max_length=20, choices=StartTimeline.choices, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:
        user_id: int

    class Meta:
        verbose_name = "profil de recherche"
        verbose_name_plural = "profils de recherche"

    def __str__(self) -> str:
        return f"Profil de recherche de {self.user.get_username()}"

    def clean(self) -> None:
        """The JSON lists: lists of strings, known choice values, bounded lengths."""
        errors: dict[str, str] = {}
        for name, allowed in (
            ("help_wanted", set(HelpWanted.values)),
            ("industries", set(Industry.values)),
            ("work_types", set(WorkType.values)),
        ):
            problem = _list_problem(getattr(self, name), allowed=allowed)
            if problem:
                errors[name] = problem
        for name, limit in (("job_titles", MAX_JOB_TITLES), ("cities", MAX_CITIES)):
            problem = _list_problem(
                getattr(self, name), limit=limit, entry_length=MAX_LIST_ENTRY_LENGTH
            )
            if problem:
                errors[name] = problem
        if (self.salary_min is None) != (not self.salary_period):
            errors["salary_period"] = "Un salaire va avec sa période, et réciproquement."
        if errors:
            raise ValidationError(errors)

    @property
    def weekly_cost(self) -> int | None:
        return weekly_cost(self.salary_min, self.salary_period)


def _list_problem(
    value: Any,
    *,
    allowed: set[str] | None = None,
    limit: int | None = None,
    entry_length: int | None = None,
) -> str:
    """Why ``value`` is not an acceptable JSON list, or ``""`` when it is."""
    if not isinstance(value, list):
        return "Une liste est attendue."
    if limit is not None and len(value) > limit:
        return f"{limit} entrées au maximum."
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            return "Chaque entrée doit être un texte non vide."
        if entry_length is not None and len(entry) > entry_length:
            return f"Chaque entrée fait {entry_length} caractères au maximum."
        if allowed is not None and entry not in allowed:
            return f"Valeur inconnue : {entry}."
    if len(set(value)) != len(value):
        return "Une même entrée ne peut pas figurer deux fois."
    return ""
