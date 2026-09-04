"""Domain model for the job-application tracker.

The vocabulary is deliberately kept in English in the code while every
human-readable label is French, so the UI reads naturally without making the
codebase awkward to extend.

Every root row — company, platform, skill gap, application, document — belongs
to an account (``owner``); events and contacts hang off their application.
Reading code always starts from ``Model.objects.for_user(user)``; on
PostgreSQL the database enforces the same boundary by itself (``rls``).
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Self, TypeVar

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, SuspiciousFileOperation
from django.db import models, transaction
from django.db.models import Case, F, IntegerField, Q, When
from django.db.models.signals import post_delete
from django.dispatch import Signal, receiver
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

if TYPE_CHECKING:
    from django.db.models.fields.related_descriptors import RelatedManager

#: Sent after an application has been handed to another account, with
#: ``application`` and ``previous_owner_id``. Extensions that copy the owner
#: on their own rows listen to it.
application_owner_changed = Signal()


_M = TypeVar("_M", bound=models.Model)


class OwnedQuerySet(models.QuerySet[_M]):
    def for_user(self, user) -> Self:
        return self.filter(owner=user)


class OwnedManager(models.Manager[_M]):
    """Explicit rather than ``OwnedQuerySet.as_manager()``: a type checker
    without the django-stubs plugin cannot see methods proxied at runtime,
    so every queryset method the code calls on ``objects`` is spelled out.

    Each ``objects`` declaration carries a targeted Pyright ignore: the stub
    declares ``Model.objects`` as a mutable class variable, which Pyright
    treats as invariant, so even a subtype of ``Manager`` is reported.
    """

    def get_queryset(self) -> OwnedQuerySet[_M]:
        return OwnedQuerySet(model=self.model, using=self._db)

    def for_user(self, user) -> OwnedQuerySet[_M]:
        return self.get_queryset().for_user(user)


def owner_field(related_name: str) -> models.ForeignKey:
    return models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name=related_name,
        verbose_name="propriétaire",
    )


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
NEXT_STATUS: dict[str, str] = {
    Status.BACKLOG: Status.TO_APPLY,
    Status.TO_APPLY: Status.SENT,
    Status.SENT: Status.SCREENING,
    Status.SCREENING: Status.INTERVIEW,
    Status.INTERVIEW: Status.TECHNICAL,
    Status.TECHNICAL: Status.OFFER,
    Status.OFFER: Status.ACCEPTED,
}

#: Visual family for each status, consumed by the CSS as `.pill--{tone}`.
STATUS_TONE: dict[str, str] = {
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
    owner = owner_field("companies")
    name = models.CharField("nom", max_length=200)
    slug = models.SlugField(max_length=220, blank=True)
    sector = models.CharField(
        "secteur", max_length=20, choices=Sector.choices, default=Sector.UNKNOWN
    )
    website = models.URLField("site web", blank=True)
    location = models.CharField("localisation", max_length=200, blank=True)
    notes = models.TextField("notes", blank=True)

    objects: ClassVar[OwnedManager[Company]] = OwnedManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    if TYPE_CHECKING:
        owner_id: int

    class Meta:
        verbose_name = "société"
        verbose_name_plural = "sociétés"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="company_name_per_owner"),
            models.UniqueConstraint(fields=["owner", "slug"], name="company_slug_per_owner"),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Company, slugify(self.name) or "societe", owner_id=self.owner_id)
        super().save(*args, **kwargs)


class Platform(models.Model):
    """A job board or channel, and what looking there actually yielded."""

    owner = owner_field("platforms")
    name = models.CharField("plateforme", max_length=200)
    url = models.URLField("adresse", blank=True)
    searched_for = models.TextField("ce que j'y ai cherché", blank=True)
    outcome = models.TextField("résultat", blank=True)
    is_lead = models.BooleanField(
        "piste à explorer",
        default=False,
        help_text="Canal identifié mais pas encore exploré.",
    )
    last_checked = models.DateField("dernière consultation", null=True, blank=True)

    objects: ClassVar[OwnedManager[Platform]] = OwnedManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    if TYPE_CHECKING:
        owner_id: int

    class Meta:
        verbose_name = "plateforme"
        verbose_name_plural = "plateformes"
        ordering = ["is_lead", "name"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="platform_name_per_owner"),
        ]

    def __str__(self) -> str:
        return self.name


class SkillGap(models.Model):
    """A competence the market keeps asking for and that is missing."""

    owner = owner_field("skill_gaps")
    name = models.CharField("compétence", max_length=200)
    demand_count = models.PositiveSmallIntegerField("offres concernées", default=0)
    demand_label = models.CharField("libellé de fréquence", max_length=120, blank=True)
    why_it_matters = models.TextField("pourquoi ça compte", blank=True)
    action_plan = models.TextField("piste", blank=True)
    status = models.CharField(
        "état", max_length=10, choices=GapStatus.choices, default=GapStatus.TODO
    )
    position = models.PositiveSmallIntegerField("ordre", default=0)

    objects: ClassVar[OwnedManager[SkillGap]] = OwnedManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    if TYPE_CHECKING:
        owner_id: int

    class Meta:
        verbose_name = "lacune"
        verbose_name_plural = "lacunes"
        ordering = ["position", "-demand_count", "name"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="skillgap_name_per_owner"),
        ]

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


class ApplicationQuerySet(OwnedQuerySet["Application"]):
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

    def stale(self, days: int, on: dt.date | None = None):
        """Sent ``days`` ago or more, still no reaction from the other side.

        The threshold is the owner's preference, so callers pass it in."""
        on = on or timezone.localdate()
        return self.filter(
            status=Status.SENT,
            applied_on__isnull=False,
            applied_on__lte=on - dt.timedelta(days=days),
        )

    def needs_attention(self, days: int, on: dt.date | None = None):
        """A follow-up that is due, or an application gone stale."""
        on = on or timezone.localdate()
        return self.filter(
            Q(follow_up_on__lte=on, status__in=IN_FLIGHT_STATUSES)
            | Q(
                status=Status.SENT,
                applied_on__isnull=False,
                applied_on__lte=on - dt.timedelta(days=days),
            )
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
        # ``nulls_last``: SQLite sorts NULL first on DESC, PostgreSQL last —
        # an unscored offer must end its column on both.
        return self.annotate(_stage=ordering).order_by(
            "_stage", F("score").desc(nulls_last=True), "company__name"
        )

    def with_related(self):
        # ``owner__preferences`` feeds ``is_stale`` / ``apply_status`` without
        # a query per row.
        return self.select_related("company", "source_platform", "owner__preferences")


class ApplicationManager(OwnedManager["Application"]):
    def get_queryset(self) -> ApplicationQuerySet:
        return ApplicationQuerySet(model=self.model, using=self._db)

    def for_user(self, user) -> ApplicationQuerySet:
        return self.get_queryset().for_user(user)

    def open(self) -> ApplicationQuerySet:
        return self.get_queryset().open()

    def in_flight(self) -> ApplicationQuerySet:
        return self.get_queryset().in_flight()

    def interviewing(self) -> ApplicationQuerySet:
        return self.get_queryset().interviewing()

    def closed(self) -> ApplicationQuerySet:
        return self.get_queryset().closed()

    def discarded(self) -> ApplicationQuerySet:
        return self.get_queryset().discarded()

    def active(self) -> ApplicationQuerySet:
        return self.get_queryset().active()

    def follow_up_due(self, on: dt.date | None = None) -> ApplicationQuerySet:
        return self.get_queryset().follow_up_due(on)

    def stale(self, days: int, on: dt.date | None = None) -> ApplicationQuerySet:
        return self.get_queryset().stale(days, on)

    def needs_attention(self, days: int, on: dt.date | None = None) -> ApplicationQuerySet:
        return self.get_queryset().needs_attention(days, on)

    def by_pipeline_order(self) -> ApplicationQuerySet:
        return self.get_queryset().by_pipeline_order()

    def with_related(self) -> ApplicationQuerySet:
        return self.get_queryset().with_related()


class Application(models.Model):
    """One job offer being tracked, from triage to outcome."""

    owner = owner_field("applications")
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="applications", verbose_name="société"
    )
    title = models.CharField("intitulé du poste", max_length=250)
    slug = models.SlugField(max_length=280, blank=True)

    location = models.CharField("lieu", max_length=200, blank=True)
    distance_km = models.PositiveSmallIntegerField(
        "distance (km)", null=True, blank=True, help_text="À vol d'oiseau depuis chez toi."
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

    objects: ClassVar[ApplicationManager] = ApplicationManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    if TYPE_CHECKING:
        # What the django-stubs plugin would derive: raw foreign-key columns
        # and the reverse managers of the satellites.
        owner_id: int
        company_id: int
        source_platform_id: int | None
        documents: RelatedManager[Document]
        events: RelatedManager[ActivityEvent]
        contacts: RelatedManager[Contact]

    class Meta:
        verbose_name = "candidature"
        verbose_name_plural = "candidatures"
        ordering = ["-score", "company__name"]
        indexes = [models.Index(fields=["status", "follow_up_on"])]
        constraints = [
            models.UniqueConstraint(fields=["owner", "slug"], name="application_slug_per_owner"),
        ]

    def __str__(self) -> str:
        return f"{self.company.name} — {self.title}"

    @classmethod
    def from_db(cls, db, field_names, values, **kwargs):
        instance = super().from_db(db, field_names, values, **kwargs)
        # Remembered so that a change of owner can be propagated on save.
        instance._loaded_owner_id = dict(zip(field_names, values)).get("owner_id")
        return instance

    def save(self, *args, **kwargs):
        if self.company_id and self.owner_id and self.company.owner_id != self.owner_id:
            # A slip here would silently show one profile's company to another.
            raise ValueError("La société et la candidature n'ont pas le même propriétaire.")
        if not self.slug:
            base = slugify(f"{self.company.name}-{self.title}")[:200] or "candidature"
            self.slug = unique_slug(Application, base, owner_id=self.owner_id)
        previous_owner_id = getattr(self, "_loaded_owner_id", None)
        super().save(*args, **kwargs)
        if previous_owner_id is not None and previous_owner_id != self.owner_id:
            # Attached documents carry a copy of the owner; extensions may too.
            self.documents.exclude(owner_id=self.owner_id).update(owner_id=self.owner_id)
            application_owner_changed.send(
                sender=Application, application=self, previous_owner_id=previous_owner_id
            )
        self._loaded_owner_id = self.owner_id

    def get_absolute_url(self) -> str:
        return reverse("tracker:application_detail", args=[self.pk])

    def owner_preferences(self):
        """The owner's thresholds, from the ``select_related`` row when loaded."""
        from accounts.services import preferences_for

        try:
            return self.owner.preferences
        except (AttributeError, ObjectDoesNotExist):
            return preferences_for(self.owner)

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
        return str(Status(nxt).label) if nxt else None

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
        # The rule lives in ``tracker.domain``; the threshold is resolved
        # lazily so a row that cannot be stale never loads the preferences.
        from tracker import domain

        return domain.is_stale(
            self,
            stale_days=lambda: self.owner_preferences().stale_after_days,
            today=timezone.localdate(),
        )

    @property
    def needs_attention(self) -> bool:
        from tracker import domain

        return domain.needs_attention(
            self,
            stale_days=lambda: self.owner_preferences().stale_after_days,
            today=timezone.localdate(),
        )

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

        Returns ``True`` when something actually changed. The rule lives in
        ``tracker.domain``; this method persists its outcome the model way
        (``save`` then ``log``). The use case ``tracker.services.change_status``
        does the same through the ports, in one transaction.
        """
        from tracker import domain

        transition = domain.plan_transition(
            self,
            new_status,
            today=timezone.localdate(),
            follow_up_days=lambda: self.owner_preferences().follow_up_days,
        )
        if transition is None:
            return False
        self.save()
        self.log(transition.event_kind, transition.event_title, note)
        return True


class ActivityEvent(models.Model):
    """A dated entry on an application's timeline."""

    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="events", verbose_name="candidature"
    )
    if TYPE_CHECKING:
        application_id: int
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


#: Width of ``Document.file``: the stored name (``upload_to`` path included)
#: must fit, and the storage port truncates while looking for a free name.
DOCUMENT_NAME_MAX_LENGTH = 400


def document_upload_to(instance: "Document", filename: str) -> str:
    # Slugs are unique per account, not globally: the account id keeps two
    # accounts' "acme-devops" folders apart on disk. A file saved before the
    # row (``document.file.save``) takes the account from the application.
    if instance.application_id:
        folder = instance.application.slug
        owner_id = instance.owner_id or instance.application.owner_id
    else:
        folder = "bibliotheque"
        owner_id = instance.owner_id
    return f"documents/{owner_id}/{folder}/{Path(filename).name}"


class Document(models.Model):
    """A file attached to an application, or a reusable base CV.

    ``owner`` is set even when the document hangs off an application (it is
    then the application's owner) so the library, the counters and the
    download view filter on one condition."""

    owner = owner_field("documents")
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
    file = models.FileField("fichier", upload_to=document_upload_to, max_length=DOCUMENT_NAME_MAX_LENGTH)
    language = models.CharField(
        "langue", max_length=2, choices=Language.choices, blank=True
    )
    is_primary = models.BooleanField("document principal", default=False)
    source_path = models.CharField("chemin d'origine", max_length=500, blank=True)
    # Recorded once at save time: on a remote provider every ``file.size``
    # is a round-trip, and the library page shows one per row.
    size_bytes = models.PositiveBigIntegerField("taille (octets)", null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    objects: ClassVar[OwnedManager[Document]] = OwnedManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    if TYPE_CHECKING:
        owner_id: int
        application_id: int | None

    class Meta:
        verbose_name = "document"
        verbose_name_plural = "documents"
        ordering = ["-is_primary", "kind", "label"]

    def __str__(self) -> str:
        return self.label

    def save(self, *args, **kwargs):
        if self.application_id:
            if not self.owner_id:
                self.owner_id = self.application.owner_id
            elif self.owner_id != self.application.owner_id:
                raise ValueError("Le document et la candidature n'ont pas le même propriétaire.")
        if self.size_bytes is None and self.file:
            # An upload not yet stored knows its size for free; a file the
            # storage already holds costs one lookup, now rather than per page.
            try:
                self.size_bytes = self.file.size
            except (OSError, ValueError):
                pass
        super().save(*args, **kwargs)

    def get_download_url(self) -> str:
        return reverse("tracker:document_download", args=[self.pk])

    @property
    def extension(self) -> str:
        return Path(self.file.name or "").suffix.lstrip(".").upper()

    @property
    def size_display(self) -> str:
        size: float | None = self.size_bytes
        if size is None:
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
    if TYPE_CHECKING:
        application_id: int
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
def delete_file_from_storage(sender, instance: Document, **kwargs):
    """Keep the storage in step with the database, cascades included.

    Through the configured adapter (disk, Azure…); a provider failure is
    logged, not raised — the row is already gone."""
    if not instance.file:
        return
    name = instance.file.name or ""
    storage = instance.file.storage

    def forget():
        try:
            # Through the port when the configured backend speaks it: it
            # already treats a name that is gone, or one no storage would
            # accept, as nothing to do. ``SuspiciousFileOperation`` is not an
            # ``OSError``, so a raw ``delete`` could abort the commit hooks.
            delete = getattr(storage, "delete_file", storage.delete)
            delete(name)
        except (OSError, SuspiciousFileOperation):
            logging.getLogger(__name__).exception("Fichier %s non supprimé du stockage.", name)

    # Only once the deletion is committed: a rollback further up (the whole
    # request is one transaction on PostgreSQL) would otherwise bring the row
    # back without its file. Outside a transaction this runs on the spot.
    transaction.on_commit(forget)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def unique_slug(model, base: str, *, owner_id: int | None) -> str:
    """Return ``base``, suffixed with a counter if the account already uses it.

    Per account, not global: the runtime role only sees the account's own
    rows (row-level security), so a global check could not be trusted — and
    the uniqueness constraints are per owner accordingly.
    """
    base = base[:200] or "item"
    candidate, counter = base, 2
    while model.objects.filter(owner_id=owner_id, slug=candidate).exists():
        suffix = f"-{counter}"
        candidate = f"{base[: 200 - len(suffix)]}{suffix}"
        counter += 1
    return candidate
