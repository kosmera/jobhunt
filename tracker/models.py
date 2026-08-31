"""Domain model for the job-application tracker.

The vocabulary is deliberately kept in English in the code while every
human-readable label is French, so the UI reads naturally without making the
codebase awkward to extend.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from django.conf import settings
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.db.models import Case, IntegerField, When
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify


# ---------------------------------------------------------------------------
# Choices
# ---------------------------------------------------------------------------


class Sector(models.TextChoices):
    PRIVATE = "private", "Privé"
    PARAPUBLIC = "parapublic", "Parapublic"
    PUBLIC = "public", "Public"
    UNKNOWN = "unknown", "Non déterminé"


class Language(models.TextChoices):
    FR = "fr", "Français"
    EN = "en", "Anglais"
    NL = "nl", "Néerlandais"


class WorkMode(models.TextChoices):
    ON_SITE = "on_site", "Sur site"
    HYBRID = "hybrid", "Hybride"
    REMOTE = "remote", "Télétravail"
    UNKNOWN = "unknown", "Non précisé"


class Status(models.TextChoices):
    """The lifecycle of an application, ordered from cold to closed."""

    BACKLOG = "backlog", "À examiner"
    TO_APPLY = "to_apply", "À postuler"
    SENT = "sent", "Candidature envoyée"
    SCREENING = "screening", "Premier contact"
    INTERVIEW = "interview", "Entretien"
    TECHNICAL = "technical", "Test technique"
    OFFER = "offer", "Offre reçue"
    ACCEPTED = "accepted", "Acceptée"
    REJECTED = "rejected", "Refusée"
    WITHDRAWN = "withdrawn", "Retirée"
    GHOSTED = "ghosted", "Sans réponse"
    DISCARDED = "discarded", "Écartée"


#: Columns of the pipeline board, in order.
PIPELINE_STATUSES = [
    Status.BACKLOG,
    Status.TO_APPLY,
    Status.SENT,
    Status.SCREENING,
    Status.INTERVIEW,
    Status.TECHNICAL,
    Status.OFFER,
]

#: Statuses that still require something from you.
OPEN_STATUSES = PIPELINE_STATUSES

#: Statuses where the ball is in the employer's court.
IN_FLIGHT_STATUSES = [
    Status.SENT,
    Status.SCREENING,
    Status.INTERVIEW,
    Status.TECHNICAL,
    Status.OFFER,
]

#: Statuses that represent an interview process actually under way.
INTERVIEWING_STATUSES = [Status.SCREENING, Status.INTERVIEW, Status.TECHNICAL]

#: Terminal statuses.
CLOSED_STATUSES = [
    Status.ACCEPTED,
    Status.REJECTED,
    Status.WITHDRAWN,
    Status.GHOSTED,
    Status.DISCARDED,
]

#: Suggested "next step" for the one-click advance button.
NEXT_STATUS = {
    Status.BACKLOG: Status.TO_APPLY,
    Status.TO_APPLY: Status.SENT,
    Status.SENT: Status.SCREENING,
    Status.SCREENING: Status.INTERVIEW,
    Status.INTERVIEW: Status.TECHNICAL,
    Status.TECHNICAL: Status.OFFER,
    Status.OFFER: Status.ACCEPTED,
}

#: Visual family for each status, consumed by the CSS as `.pill--{tone}`.
STATUS_TONE = {
    Status.BACKLOG: "slate",
    Status.TO_APPLY: "amber",
    Status.SENT: "blue",
    Status.SCREENING: "violet",
    Status.INTERVIEW: "violet",
    Status.TECHNICAL: "violet",
    Status.OFFER: "green",
    Status.ACCEPTED: "green",
    Status.REJECTED: "red",
    Status.WITHDRAWN: "slate",
    Status.GHOSTED: "slate",
    Status.DISCARDED: "slate",
}


class DocumentKind(models.TextChoices):
    CV = "cv", "CV"
    COVER_LETTER = "cover_letter", "Lettre de motivation"
    POSTING = "posting", "Annonce"
    PORTFOLIO = "portfolio", "Portfolio"
    OTHER = "other", "Autre"


class EventKind(models.TextChoices):
    NOTE = "note", "Note"
    STATUS = "status", "Changement de statut"
    APPLIED = "applied", "Candidature envoyée"
    FOLLOW_UP = "follow_up", "Relance"
    EMAIL = "email", "E-mail"
    CALL = "call", "Appel"
    INTERVIEW = "interview", "Entretien"
    TEST = "test", "Test technique"
    OFFER = "offer", "Offre"
    REJECTION = "rejection", "Refus"


class GapStatus(models.TextChoices):
    TODO = "todo", "À travailler"
    DOING = "doing", "En cours"
    DONE = "done", "Comblée"


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------


class Company(models.Model):
    name = models.CharField("nom", max_length=200, unique=True)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    sector = models.CharField(
        "secteur", max_length=20, choices=Sector.choices, default=Sector.UNKNOWN
    )
    website = models.URLField("site web", blank=True)
    location = models.CharField("localisation", max_length=200, blank=True)
    notes = models.TextField("notes", blank=True)

    class Meta:
        verbose_name = "société"
        verbose_name_plural = "sociétés"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Company, slugify(self.name) or "societe")
        super().save(*args, **kwargs)


class Platform(models.Model):
    """A job board or channel, and what looking there actually yielded."""

    name = models.CharField("plateforme", max_length=200, unique=True)
    url = models.URLField("adresse", blank=True)
    searched_for = models.TextField("ce que j'y ai cherché", blank=True)
    outcome = models.TextField("résultat", blank=True)
    is_lead = models.BooleanField(
        "piste à explorer",
        default=False,
        help_text="Canal identifié mais pas encore exploré.",
    )
    last_checked = models.DateField("dernière consultation", null=True, blank=True)

    class Meta:
        verbose_name = "plateforme"
        verbose_name_plural = "plateformes"
        ordering = ["is_lead", "name"]

    def __str__(self) -> str:
        return self.name


class SkillGap(models.Model):
    """A competence the market keeps asking for and that is missing."""

    name = models.CharField("compétence", max_length=200, unique=True)
    demand_count = models.PositiveSmallIntegerField("offres concernées", default=0)
    demand_label = models.CharField("libellé de fréquence", max_length=120, blank=True)
    why_it_matters = models.TextField("pourquoi ça compte", blank=True)
    action_plan = models.TextField("piste", blank=True)
    status = models.CharField(
        "état", max_length=10, choices=GapStatus.choices, default=GapStatus.TODO
    )
    position = models.PositiveSmallIntegerField("ordre", default=0)

    class Meta:
        verbose_name = "lacune"
        verbose_name_plural = "lacunes"
        ordering = ["position", "-demand_count", "name"]

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


class ApplicationQuerySet(models.QuerySet):
    def open(self):
        return self.filter(status__in=OPEN_STATUSES)

    def in_flight(self):
        return self.filter(status__in=IN_FLIGHT_STATUSES)

    def interviewing(self):
        return self.filter(status__in=INTERVIEWING_STATUSES)

    def closed(self):
        return self.filter(status__in=CLOSED_STATUSES)

    def discarded(self):
        return self.filter(status=Status.DISCARDED)

    def active(self):
        """Everything except offers written off during the initial triage."""
        return self.exclude(status=Status.DISCARDED)

    def follow_up_due(self, on: dt.date | None = None):
        on = on or timezone.localdate()
        return self.filter(
            follow_up_on__isnull=False,
            follow_up_on__lte=on,
            status__in=IN_FLIGHT_STATUSES,
        )

    def stale(self, days: int | None = None, on: dt.date | None = None):
        """Sent a while ago, still no reaction from the other side."""
        days = settings.STALE_AFTER_DAYS if days is None else days
        on = on or timezone.localdate()
        return self.filter(
            status=Status.SENT,
            applied_on__isnull=False,
            applied_on__lte=on - dt.timedelta(days=days),
        )

    def by_pipeline_order(self):
        """Order by lifecycle stage, then by how promising the offer is."""
        ordering = Case(
            *[
                When(status=status, then=index)
                for index, status in enumerate(Status.values)
            ],
            output_field=IntegerField(),
        )
        return self.annotate(_stage=ordering).order_by("_stage", "-score", "company__name")

    def with_related(self):
        return self.select_related("company", "source_platform")


class Application(models.Model):
    """One job offer being tracked, from triage to outcome."""

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="applications", verbose_name="société"
    )
    title = models.CharField("intitulé du poste", max_length=250)
    slug = models.SlugField(max_length=280, unique=True, blank=True)

    location = models.CharField("lieu", max_length=200, blank=True)
    distance_km = models.PositiveSmallIntegerField(
        "distance (km)", null=True, blank=True, help_text="À vol d'oiseau depuis Nivelles."
    )
    work_mode = models.CharField(
        "mode de travail", max_length=10, choices=WorkMode.choices, default=WorkMode.UNKNOWN
    )

    score = models.PositiveSmallIntegerField(
        "compatibilité (%)",
        null=True,
        blank=True,
        help_text="Estimation de recouvrement entre l'annonce et ton profil, de 0 à 100.",
    )
    source_platform = models.ForeignKey(
        Platform,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="applications",
        verbose_name="source",
    )
    url = models.URLField("lien de l'offre", max_length=500, blank=True)
    cv_language = models.CharField(
        "langue du CV", max_length=2, choices=Language.choices, default=Language.FR
    )

    status = models.CharField(
        "statut", max_length=20, choices=Status.choices, default=Status.BACKLOG, db_index=True
    )
    discovered_on = models.DateField("offre relevée le", null=True, blank=True)
    applied_on = models.DateField("date de candidature", null=True, blank=True)
    follow_up_on = models.DateField("relance prévue", null=True, blank=True)
    closed_on = models.DateField("clôturée le", null=True, blank=True)

    summary = models.TextField("résumé", blank=True, help_text="La phrase qui résume l'offre.")
    strengths = models.TextField("ce qui joue pour toi", blank=True)
    weaknesses = models.TextField("ce qui joue contre toi", blank=True)
    strategy = models.TextField("comment aborder cette candidature", blank=True)
    posting_raw = models.TextField("texte de l'annonce", blank=True)
    notes_raw = models.TextField("notes d'origine", blank=True)
    personal_notes = models.TextField("mes notes", blank=True)
    discard_reason = models.TextField("raison de l'écartement", blank=True)

    legacy_folder = models.CharField("dossier d'origine", max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ApplicationQuerySet.as_manager()

    class Meta:
        verbose_name = "candidature"
        verbose_name_plural = "candidatures"
        ordering = ["-score", "company__name"]
        indexes = [models.Index(fields=["status", "follow_up_on"])]

    def __str__(self) -> str:
        return f"{self.company.name} — {self.title}"

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(f"{self.company.name}-{self.title}")[:200] or "candidature"
            self.slug = unique_slug(Application, base)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("tracker:application_detail", args=[self.pk])

    # -- lifecycle ---------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def is_in_flight(self) -> bool:
        return self.status in IN_FLIGHT_STATUSES

    @property
    def is_closed(self) -> bool:
        return self.status in CLOSED_STATUSES

    @property
    def is_discarded(self) -> bool:
        return self.status == Status.DISCARDED

    @property
    def status_tone(self) -> str:
        return STATUS_TONE.get(self.status, "slate")

    @property
    def next_status(self) -> str | None:
        return NEXT_STATUS.get(self.status)

    @property
    def next_status_label(self) -> str | None:
        nxt = self.next_status
        return Status(nxt).label if nxt else None

    # -- timing ------------------------------------------------------------

    @property
    def days_since_applied(self) -> int | None:
        if not self.applied_on:
            return None
        return (timezone.localdate() - self.applied_on).days

    @property
    def days_until_follow_up(self) -> int | None:
        if not self.follow_up_on:
            return None
        return (self.follow_up_on - timezone.localdate()).days

    @property
    def follow_up_state(self) -> str | None:
        """One of ``overdue`` / ``today`` / ``soon`` / ``later``."""
        days = self.days_until_follow_up
        if days is None or not self.is_in_flight:
            return None
        if days < 0:
            return "overdue"
        if days == 0:
            return "today"
        if days <= 3:
            return "soon"
        return "later"

    @property
    def is_stale(self) -> bool:
        days = self.days_since_applied
        return (
            self.status == Status.SENT
            and days is not None
            and days >= settings.STALE_AFTER_DAYS
        )

    @property
    def needs_attention(self) -> bool:
        return self.follow_up_state in {"overdue", "today"} or self.is_stale

    # -- documents ---------------------------------------------------------

    @property
    def primary_cv(self):
        return (
            self.documents.filter(kind=DocumentKind.CV)
            .order_by("-is_primary", "-uploaded_at")
            .first()
        )

    @property
    def score_band(self) -> str:
        """Coarse bucket used to colour the score badge."""
        if self.score is None:
            return "none"
        if self.score >= 80:
            return "high"
        if self.score >= 65:
            return "mid"
        return "low"

    def log(self, kind: str, title: str, detail: str = "", on: dt.date | None = None):
        return ActivityEvent.objects.create(
            application=self,
            kind=kind,
            title=title,
            detail=detail,
            happened_on=on or timezone.localdate(),
        )

    def apply_status(self, new_status: str, *, note: str = "") -> bool:
        """Move to ``new_status``, keeping dates and the timeline in step.

        Returns ``True`` when something actually changed.
        """
        if new_status == self.status or new_status not in Status.values:
            return False

        previous = Status(self.status).label
        today = timezone.localdate()
        self.status = new_status

        if new_status in IN_FLIGHT_STATUSES and not self.applied_on:
            self.applied_on = today
        if new_status == Status.SENT and not self.follow_up_on:
            self.follow_up_on = today + dt.timedelta(days=settings.DEFAULT_FOLLOW_UP_DAYS)
        if new_status in CLOSED_STATUSES:
            self.closed_on = self.closed_on or today
            self.follow_up_on = None
        else:
            self.closed_on = None

        self.save()

        kind = {
            Status.SENT: EventKind.APPLIED,
            Status.INTERVIEW: EventKind.INTERVIEW,
            Status.SCREENING: EventKind.CALL,
            Status.TECHNICAL: EventKind.TEST,
            Status.OFFER: EventKind.OFFER,
            Status.REJECTED: EventKind.REJECTION,
        }.get(new_status, EventKind.STATUS)
        self.log(kind, f"{previous} → {Status(new_status).label}", note)
        return True


class ActivityEvent(models.Model):
    """A dated entry on an application's timeline."""

    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="events", verbose_name="candidature"
    )
    happened_on = models.DateField("date", default=timezone.localdate)
    kind = models.CharField(
        "type", max_length=20, choices=EventKind.choices, default=EventKind.NOTE
    )
    title = models.CharField("intitulé", max_length=250)
    detail = models.TextField("détail", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "événement"
        verbose_name_plural = "événements"
        ordering = ["-happened_on", "-created_at"]

    def __str__(self) -> str:
        return f"{self.happened_on} · {self.title}"


def document_upload_to(instance: "Document", filename: str) -> str:
    folder = instance.application.slug if instance.application_id else "bibliotheque"
    return f"documents/{folder}/{Path(filename).name}"


class Document(models.Model):
    """A file attached to an application, or a reusable base CV."""

    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name="documents",
        null=True,
        blank=True,
        verbose_name="candidature",
        help_text="Laisser vide pour un document de la bibliothèque (CV génériques).",
    )
    kind = models.CharField(
        "type", max_length=20, choices=DocumentKind.choices, default=DocumentKind.CV
    )
    label = models.CharField("libellé", max_length=250)
    file = models.FileField("fichier", upload_to=document_upload_to, max_length=400)
    language = models.CharField(
        "langue", max_length=2, choices=Language.choices, blank=True
    )
    is_primary = models.BooleanField("document principal", default=False)
    source_path = models.CharField("chemin d'origine", max_length=500, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "document"
        verbose_name_plural = "documents"
        ordering = ["-is_primary", "kind", "label"]

    def __str__(self) -> str:
        return self.label

    @property
    def extension(self) -> str:
        return Path(self.file.name).suffix.lstrip(".").upper()

    @property
    def size_display(self) -> str:
        try:
            size = self.file.size
        except (OSError, ValueError):
            return "—"
        for unit in ("o", "Ko", "Mo"):
            if size < 1024 or unit == "Mo":
                return f"{size:.0f} {unit}" if unit == "o" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} Mo"

    @property
    def is_library_document(self) -> bool:
        return self.application_id is None


class Contact(models.Model):
    """Someone on the other side: recruiter, hiring manager, referral."""

    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="contacts", verbose_name="candidature"
    )
    name = models.CharField("nom", max_length=200)
    role = models.CharField("fonction", max_length=200, blank=True)
    email = models.EmailField("e-mail", blank=True)
    phone = models.CharField("téléphone", max_length=60, blank=True)
    linkedin = models.URLField("LinkedIn", blank=True)
    notes = models.TextField("notes", blank=True)

    class Meta:
        verbose_name = "contact"
        verbose_name_plural = "contacts"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


@receiver(post_delete, sender=Document)
def delete_file_from_disk(sender, instance: Document, **kwargs):
    """Keep MEDIA_ROOT in step with the database, cascades included."""
    if instance.file:
        instance.file.delete(save=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def unique_slug(model, base: str) -> str:
    """Return ``base``, suffixed with a counter if the slug is already taken."""
    base = base[:200] or "item"
    candidate, counter = base, 2
    while model.objects.filter(slug=candidate).exists():
        suffix = f"-{counter}"
        candidate = f"{base[: 200 - len(suffix)]}{suffix}"
        counter += 1
    return candidate
