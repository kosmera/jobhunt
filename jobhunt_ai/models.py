"""Modèles du copilote : profil candidat, exécutions d'agents et résultats.

Même convention que le cœur : identifiants en anglais, libellés en français.
Les structures riches produites par le modèle (compétences, expériences…)
vivent en JSON — c'est le format naturel de la sortie structurée, et le
matching se fait de toute façon par le modèle, pas par des requêtes SQL.

Comme dans le cœur, chaque racine appartient à un profil (``owner``) :
profils candidats, exécutions et pistes. Rapports et CV générés passent par
leur candidature.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse

from django.utils import timezone

if TYPE_CHECKING:
    from django.db.models.fields.related_descriptors import RelatedManager


def owner_field(related_name: str) -> models.ForeignKey:
    return models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name=related_name,
        verbose_name="propriétaire",
    )


class UserAIQuota(models.Model):
    """Attempted provider requests per UTC calendar month and fixed minute."""

    owner = owner_field("ai_quotas")
    month = models.DateField()
    requests = models.PositiveIntegerField(default=0)
    minute_started_at = models.DateTimeField()
    minute_requests = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "month"], name="ai_quota_owner_month"),
        ]


class RunKind(models.TextChoices):
    PARSE_CV = "parse_cv", "Analyse de CV"
    MATCH = "match", "Évaluation de compatibilité"
    GENERATE_CV = "generate_cv", "Génération de CV"
    SCOUT = "scout", "Veille d'offres"


class RunStatus(models.TextChoices):
    PENDING = "pending", "En attente"
    RUNNING = "running", "En cours"
    SUCCEEDED = "succeeded", "Terminée"
    FAILED = "failed", "Échouée"


#: Préfixe du résumé écrit sur la candidature à l'import d'une piste. Il
#: marque un texte de pré-tri, que l'évaluation fine remplacera par son propre
#: verdict — sans quoi la justification d'un score resterait affichée à côté
#: d'un score qui, lui, a changé. Un résumé rédigé à la main ne porte pas ce
#: préfixe et n'est donc jamais écrasé.
LEAD_SUMMARY_PREFIX = "Pré-tri du copilote : "


class LeadStatus(models.TextChoices):
    NEW = "new", "À trier"
    IMPORTED = "imported", "Importée"
    DISMISSED = "dismissed", "Écartée"


class CandidateProfile(models.Model):
    """Le contenu structuré d'un CV, extrait par l'agent d'analyse."""

    owner = owner_field("ai_candidate_profiles")
    label = models.CharField("libellé", max_length=200)
    language = models.CharField("langue", max_length=2, blank=True)
    is_primary = models.BooleanField("profil principal", default=False)

    full_name = models.CharField("nom complet", max_length=200, blank=True)
    headline = models.CharField("titre professionnel", max_length=250, blank=True)
    email = models.EmailField("e-mail", blank=True)
    phone = models.CharField("téléphone", max_length=60, blank=True)
    location = models.CharField("localisation", max_length=200, blank=True)
    links = models.JSONField("liens", default=list, blank=True)

    summary = models.TextField("résumé", blank=True)
    skills = models.JSONField("compétences", default=list, blank=True)
    experiences = models.JSONField("expériences", default=list, blank=True)
    education = models.JSONField("formation", default=list, blank=True)
    languages = models.JSONField("langues", default=list, blank=True)
    certifications = models.JSONField("certifications", default=list, blank=True)
    extras = models.JSONField("divers", default=list, blank=True)
    #: Grille de tri dérivée du profil, écrite à la première veille qui en a
    #: besoin (voir ``agents/qualifications.py``). Vide tant qu'aucune veille
    #: n'a tourné ; un nouveau CV donne un nouveau profil, donc une grille
    #: fraîche, sans invalidation à gérer.
    qualifications = models.JSONField("grille de qualifications", default=dict, blank=True)

    raw_text = models.TextField("texte source", blank=True)
    source_document = models.ForeignKey(
        "tracker.Document",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_profiles",
        verbose_name="document d'origine",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:
        # Posé par Django à l'exécution ; django-stubs ne le déduit pas sans
        # le greffon mypy, comme pour ``AgentRun`` plus bas.
        owner_id: int

    class Meta:
        verbose_name = "profil candidat"
        verbose_name_plural = "profils candidats"
        ordering = ["-is_primary", "-updated_at"]

    def __str__(self) -> str:
        return self.label

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_primary:
            # Un seul profil principal par compte.
            CandidateProfile.objects.filter(owner_id=self.owner_id, is_primary=True).exclude(
                pk=self.pk
            ).update(is_primary=False)

    @classmethod
    def primary(cls, user) -> "CandidateProfile | None":
        """Le profil principal du compte, à défaut le plus récent."""
        return cls.objects.filter(owner=user).order_by("-is_primary", "-updated_at").first()

    @property
    def skill_names(self) -> list[str]:
        return [s.get("name", "") for s in self.skills if isinstance(s, dict)]

    @property
    def skills_by_category(self) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = {}
        for skill in self.skills:
            if isinstance(skill, dict):
                groups.setdefault(skill.get("category") or "Autres", []).append(skill)
        return groups


class AgentRun(models.Model):
    """Une exécution d'agent, suivie de son lancement à son verdict."""

    owner = owner_field("ai_agent_runs")
    kind = models.CharField("type", max_length=20, choices=RunKind.choices)
    status = models.CharField(
        "état", max_length=10, choices=RunStatus.choices, default=RunStatus.PENDING,
        db_index=True,
    )
    params = models.JSONField("paramètres", default=dict, blank=True)
    result = models.JSONField("résultat", default=dict, blank=True)
    error = models.TextField("erreur", blank=True)
    task_id = models.CharField(max_length=32, null=True, blank=True, unique=True)
    deadline_at = models.DateTimeField(null=True, blank=True, db_index=True)
    phase = models.CharField("étape", max_length=100, blank=True)
    progress_current = models.PositiveIntegerField(default=0)
    progress_total = models.PositiveIntegerField(null=True, blank=True)
    fanout_started_at = models.DateTimeField(null=True, blank=True)
    fanout_context = models.JSONField(default=dict, blank=True)

    application = models.ForeignKey(
        "tracker.Application",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ai_runs",
        verbose_name="candidature",
    )
    profile = models.ForeignKey(
        CandidateProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
        verbose_name="profil",
    )

    model_id = models.CharField("modèle", max_length=100, blank=True)
    input_tokens = models.PositiveIntegerField("jetons d'entrée", default=0)
    output_tokens = models.PositiveIntegerField("jetons de sortie", default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField("démarrée le", null=True, blank=True)
    finished_at = models.DateTimeField("terminée le", null=True, blank=True)

    if TYPE_CHECKING:
        # Django pose ces attributs à l'exécution (clés étrangères brutes et
        # libellés de « choices ») ; django-stubs ne sait pas les déduire sans
        # le greffon mypy, on les déclare donc pour Pyright.
        application_id: int | None
        profile_id: int | None
        owner_id: int
        targets: RelatedManager[ScoutTarget]

        def get_kind_display(self) -> str: ...
        def get_status_display(self) -> str: ...

    class Meta:
        verbose_name = "exécution d'agent"
        verbose_name_plural = "exécutions d'agents"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["owner", "status", "kind", "-created_at"],
                name="ai_run_owner_state_kind_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} · {self.get_status_display()}"

    def get_absolute_url(self) -> str:
        return reverse("jobhunt_ai:run_status", args=[self.pk])

    @property
    def is_finished(self) -> bool:
        return self.status in {RunStatus.SUCCEEDED, RunStatus.FAILED}

    @property
    def duration_seconds(self) -> int | None:
        if not self.started_at or not self.finished_at:
            return None
        return int((self.finished_at - self.started_at).total_seconds())

    def mark_succeeded(self, result: dict | None = None):
        self.status = RunStatus.SUCCEEDED
        self.result = result or {}
        self.finished_at = timezone.now()
        type(self).objects.filter(
            pk=self.pk, owner_id=self.owner_id, status=RunStatus.RUNNING,
            fanout_started_at__isnull=True,
        ).update(status=self.status, result=self.result, error="", finished_at=self.finished_at)

    def mark_failed(self, message: str, *, quota: dict | None = None):
        self.status = RunStatus.FAILED
        self.error = message
        self.finished_at = timezone.now()
        extra = {}
        if quota is not None:
            self.result = {**self.result, "quota": quota}
            extra["result"] = self.result
        type(self).objects.filter(
            pk=self.pk, owner_id=self.owner_id,
            status__in=[RunStatus.PENDING, RunStatus.RUNNING],
            fanout_started_at__isnull=True,
        ).update(status=self.status, error=self.error, finished_at=self.finished_at, **extra)


class TargetStatus(models.TextChoices):
    PENDING = "pending", "En attente"
    SCRAPING = "scraping", "Lecture"
    SCRAPED = "scraped", "À analyser"
    ANALYZING = "analyzing", "Analyse"
    SUCCEEDED = "succeeded", "Terminée"
    FAILED = "failed", "Échouée"


class ScoutTarget(models.Model):
    """One independent Chain; private handoff data stays behind the run's RLS."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="targets")
    key = models.CharField(max_length=64)
    source = models.JSONField()
    status = models.CharField(max_length=10, choices=TargetStatus.choices, default=TargetStatus.PENDING)
    scraped_text = models.TextField(blank=True)
    created_leads = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    deadline_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "key"], name="ai_target_run_key_uniq")]
        indexes = [
            models.Index(fields=["run", "status", "deadline_at"], name="ai_target_state_deadline_idx"),
        ]


class MatchReport(models.Model):
    """Le verdict de l'agent d'évaluation pour une candidature donnée."""

    application = models.ForeignKey(
        "tracker.Application",
        on_delete=models.CASCADE,
        related_name="ai_match_reports",
        verbose_name="candidature",
    )
    profile = models.ForeignKey(
        CandidateProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="match_reports",
        verbose_name="profil",
    )
    run = models.ForeignKey(
        AgentRun, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="match_reports", verbose_name="exécution",
    )

    score = models.PositiveSmallIntegerField("compatibilité (%)")
    summary = models.TextField("verdict", blank=True)
    strengths = models.TextField("ce qui joue pour toi", blank=True)
    weaknesses = models.TextField("ce qui joue contre toi", blank=True)
    strategy = models.TextField("comment aborder cette candidature", blank=True)

    matched_skills = models.JSONField("compétences couvertes", default=list, blank=True)
    partial_skills = models.JSONField("compétences partielles", default=list, blank=True)
    missing_skills = models.JSONField("compétences manquantes", default=list, blank=True)
    gaps = models.JSONField("lacunes", default=list, blank=True)

    model_id = models.CharField("modèle", max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "rapport de compatibilité"
        verbose_name_plural = "rapports de compatibilité"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.application} · {self.score} %"


class OfferLead(models.Model):
    """Une offre repérée par la veille, en attente de triage."""

    owner = owner_field("ai_offer_leads")
    status = models.CharField(
        "état", max_length=10, choices=LeadStatus.choices, default=LeadStatus.NEW,
        db_index=True,
    )
    title = models.CharField("intitulé", max_length=250)
    company_name = models.CharField("société", max_length=200)
    location = models.CharField("lieu", max_length=200, blank=True)
    url = models.URLField("lien", max_length=500, blank=True)
    source_name = models.CharField("source", max_length=100, blank=True)
    published_on = models.DateField("publiée le", null=True, blank=True)
    description = models.TextField("description", blank=True)
    language = models.CharField("langue", max_length=2, blank=True)

    # « pré-tri » et non « compatibilité » : la note vient d'un extrait
    # d'annonce, pas de l'annonce entière comme celle de ``MatchReport``.
    score = models.PositiveSmallIntegerField("pré-tri (%)", null=True, blank=True)
    score_reason = models.TextField("justification", blank=True)
    score_confidence = models.CharField("fiabilité du pré-tri", max_length=20, blank=True)
    score_blockers = models.JSONField("points bloquants", default=list, blank=True)

    run = models.ForeignKey(
        AgentRun, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="leads", verbose_name="exécution",
    )
    application = models.ForeignKey(
        "tracker.Application",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_leads",
        verbose_name="candidature créée",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    if TYPE_CHECKING:
        application_id: int | None

    class Meta:
        verbose_name = "piste d'offre"
        verbose_name_plural = "pistes d'offres"
        ordering = ["-score", "-created_at"]

    def __str__(self) -> str:
        return f"{self.company_name} — {self.title}"

    @property
    def score_band(self) -> str:
        if self.score is None:
            return "none"
        if self.score >= 80:
            return "high"
        if self.score >= 65:
            return "mid"
        return "low"


class GeneratedCV(models.Model):
    """Un CV ATS produit pour une candidature, avec son contenu structuré."""

    application = models.ForeignKey(
        "tracker.Application",
        on_delete=models.CASCADE,
        related_name="ai_generated_cvs",
        verbose_name="candidature",
    )
    profile = models.ForeignKey(
        CandidateProfile, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="generated_cvs", verbose_name="profil",
    )
    run = models.ForeignKey(
        AgentRun, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="generated_cvs", verbose_name="exécution",
    )
    document = models.ForeignKey(
        "tracker.Document",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_generated_cvs",
        verbose_name="document",
    )

    language = models.CharField("langue", max_length=2, blank=True)
    content = models.JSONField("contenu", default=dict, blank=True)
    model_id = models.CharField("modèle", max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "CV généré"
        verbose_name_plural = "CV générés"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"CV {self.language or '?'} — {self.application}"
