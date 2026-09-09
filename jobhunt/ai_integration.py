"""JobHunt's adapter for the reusable AI app.

All tracker/account/security schema knowledge lives here. The AI package
only sees its own HostBackend contract and immutable data objects.
"""

from contextlib import contextmanager, suppress

import rls
from django.core.files.base import ContentFile
from django.utils import timezone

from accounts.services import has_premium, preferences_for, profile_for
from jobhunt_ai.backends import (
    ApplicationData,
    CVSubmission,
    DocumentData,
    HostBackend,
    Identity,
    UnsupportedFormat,
)
from jobhunt_ai.models import LEAD_SUMMARY_PREFIX, MatchReport
from tracker import privacy, services
from tracker.adapters import storage
from tracker.adapters.document_text import UnsupportedFormat as ReaderUnsupportedFormat
from tracker.forms import company_for
from tracker.models import (
    Application,
    Document,
    DocumentKind,
    EventKind,
    Platform,
    SkillGap,
    Status,
)


class JobHuntBackend(HostBackend):
    """Preserve existing JobHunt behavior behind an explicit host boundary."""

    def has_copilot_access(self, user):
        return has_premium(user)

    def ready(self):
        from jobhunt_ai.signals import cv_generated, match_completed

        for label in ("CandidateProfile", "AgentRun", "OfferLead"):
            rls.register(f"jobhunt_ai.{label}", owner="owner")
        for label in ("MatchReport", "GeneratedCV"):
            rls.register(f"jobhunt_ai.{label}", via="application")
        rls.register("jobhunt_ai.ScoutTarget", via="run")
        for label in ("OrmQ", "Task", "Schedule"):
            rls.exempt(f"django_q.{label}")
        match_completed.connect(
            apply_match_report,
            dispatch_uid="jobhunt.ai.match_completed",
            weak=False,
        )
        cv_generated.connect(
            record_generated_cv,
            dispatch_uid="jobhunt.ai.cv_generated",
            weak=False,
        )

    def as_user(self, owner_id):
        return rls.as_user(owner_id)

    def applications(self, owner_id):
        return Application.objects.filter(owner_id=owner_id).select_related(
            "company", "owner"
        )

    def documents(self, owner_id):
        return Document.objects.filter(
            owner_id=owner_id, kind=DocumentKind.CV
        ).order_by("-is_primary", "label")

    def application_data(self, application):
        return ApplicationData(
            pk=application.pk,
            owner_id=application.owner_id,
            title=application.title,
            company_name=application.company.name,
            location=application.location,
            posting_url=application.url,
            posting_text=application.posting_raw,
            summary=application.summary,
            language=application.cv_language,
            detail_url=application.get_absolute_url(),
        )

    def document_data(self, document):
        return DocumentData(
            label=document.label,
            filename=document.file.name or "",
            language=document.language,
            download_url=document.get_download_url(),
        )

    def identity(self, user):
        profile = profile_for(user)
        return Identity(
            full_name=profile.display_name or user.get_username(),
            email=user.email,
            phone=profile.phone,
            location=profile.location,
        )

    def search_preferences(self, user):
        return profile_for(user).location, preferences_for(user).search_radius_km

    def prepare_cv(self, user, *, document=None, upload=None, label=""):
        profile = profile_for(user)
        known = privacy.known_identity(
            display_name=profile.display_name,
            username=user.get_username(),
            email=user.email,
            location=profile.location,
        )
        if document is None:
            if upload is None:
                raise ValueError("Provide a CV document or an uploaded file.")
            collector = _CVCollector()
            services.ingest_cv(
                user,
                None,
                Document(kind=DocumentKind.CV),
                upload=upload,
                label=label or upload.name,
                known=known,
                analyzer=collector,
            )
            return collector.submission
        document = self.documents(user.pk).get(pk=document.pk)
        try:
            text = storage().extract_and_anonymize_text(
                document.file.name or "", known=known
            )
        except ReaderUnsupportedFormat as exc:
            raise UnsupportedFormat(str(exc)) from exc
        if len(text.text.strip()) < services.MIN_TEXT_LENGTH:
            return None
        return CVSubmission(
            document.pk,
            privacy.anonymize(label or document.label, known=known).text,
            document.language,
            text.text,
            dict(text.redactions),
        )

    def cache_posting_text(self, application, text):
        with rls.as_user(application.owner_id):
            application.posting_raw = text
            application.save(update_fields=["posting_raw", "updated_at"])

    def import_lead(self, user, lead):
        company = company_for(user, lead.company_name.strip() or "Société inconnue")
        platform = None
        if lead.source_name:
            platform, _ = Platform.objects.get_or_create(
                owner=user, name=lead.source_name
            )

        application = Application.objects.create(
            owner=user,
            company=company,
            title=lead.title,
            location=lead.location,
            url=lead.url,
            score=lead.score,
            cv_language=lead.language or "fr",
            status=Status.BACKLOG,
            discovered_on=timezone.localdate(),
            summary=f"{LEAD_SUMMARY_PREFIX}{lead.score_reason}"
            if lead.score_reason
            else "",
            posting_raw=lead.description,
            source_platform=platform,
        )
        application.log(EventKind.NOTE, "Offre repérée par le copilote")

        return application

    @contextmanager
    def generated_document(self, application, payload, language):
        document = Document(
            owner=application.owner,
            application=application,
            kind=DocumentKind.CV,
            label=f"CV IA — {application.company.name} ({language.upper()})",
            language=language,
        )
        document.file.save(
            f"cv-ia-{application.slug}-{language}.docx",
            ContentFile(payload),
            save=False,
        )
        document.size_bytes = len(payload)
        try:
            with rls.as_user(application.owner_id):
                document.save()
                yield document
        except BaseException:
            with suppress(OSError):
                storage().delete_file(document.file.name or "")
            raise


class _CVCollector:
    """Capture already anonymized intake without invoking or knowing an agent."""

    submission = None

    def analyze_cv(self, owner, *, document_id, label, language, text):
        self.submission = CVSubmission(
            document_id,
            label,
            language,
            text.text,
            dict(text.redactions),
        )


def apply_match_report(sender, report, **kwargs):
    """Project a completed AI report into the host's application and gap tables."""
    application = report.application
    verdict = report
    application.score = verdict.score
    update_fields = ["score", "updated_at"]
    # Les champs d'analyse rédigés à la main priment : ne remplir que les vides.
    for field, value in (
        ("strengths", verdict.strengths),
        ("weaknesses", verdict.weaknesses),
        ("strategy", verdict.strategy),
    ):
        if not getattr(application, field).strip() and value.strip():
            setattr(application, field, value)
            update_fields.append(field)
    # Le résumé est affiché juste à côté du score sur la fiche : celui écrit à
    # l'import justifie le pré-tri, pas le score qu'on vient de calculer.
    if summary_is_replaceable(application) and verdict.summary.strip():
        application.summary = verdict.summary
        update_fields.append("summary")
    application.save(update_fields=update_fields)
    application.log(
        EventKind.NOTE,
        f"Compatibilité évaluée par le copilote : {verdict.score} %",
        verdict.summary,
    )

    sync_skill_gaps(verdict, application.owner_id)


def summary_is_replaceable(application) -> bool:
    """Vrai si le résumé de la candidature est vide ou vient du pré-tri.

    Un résumé rédigé à la main ne porte pas le préfixe et reste intact, comme
    les autres champs d'analyse.
    """
    summary = application.summary.strip()
    return not summary or summary.startswith(LEAD_SUMMARY_PREFIX)


def sync_skill_gaps(verdict, owner_id: int) -> None:
    """Répercute les lacunes dans la table SkillGap de la page Analyse du compte.

    Une entrée existante n'est jamais écrasée : seul le compteur de demande
    est recalculé, les notes rédigées à la main restent intactes.
    """
    # Comptage exact en Python : un LIKE sur le JSON sérialisé ferait matcher
    # « Go » dans « Django » ou « SQL » dans « PostgreSQL ».
    reports = list(
        MatchReport.objects.filter(application__owner_id=owner_id).values_list(
            "application_id", "missing_skills"
        )
    )

    def demand_for(skill: str) -> int:
        target = skill.strip().casefold()
        return len(
            {
                application_id
                for application_id, missing in reports
                if any(
                    isinstance(entry, str) and entry.strip().casefold() == target
                    for entry in (missing or [])
                )
            }
        )

    for gap in verdict.gaps:
        name = (gap["name"] if isinstance(gap, dict) else gap.name).strip()
        if not name:
            continue
        demand = demand_for(name) or 1
        existing = SkillGap.objects.filter(owner_id=owner_id, name__iexact=name).first()
        if existing:
            existing.demand_count = max(existing.demand_count, demand)
            existing.demand_label = f"{existing.demand_count} offre(s) évaluée(s)"
            existing.save(update_fields=["demand_count", "demand_label"])
        else:
            SkillGap.objects.create(
                owner_id=owner_id,
                name=name,
                demand_count=demand,
                demand_label=f"{demand} offre(s) évaluée(s)",
                why_it_matters=gap["why_it_matters"]
                if isinstance(gap, dict)
                else gap.why_it_matters,
                action_plan=gap["action_plan"]
                if isinstance(gap, dict)
                else gap.action_plan,
            )


def record_generated_cv(sender, generated, **kwargs):
    generated.application.log(
        EventKind.NOTE,
        f"CV généré par le copilote ({generated.language.upper()})",
    )


def attach_legacy_owners(apps, schema_editor):
    """Historical account migration, using frozen models and the migration DB."""
    from accounts.migration_helpers import owner_for_orphans

    alias = schema_editor.connection.alias
    AgentRun = apps.get_model("jobhunt_ai", "AgentRun")
    models = [
        apps.get_model("jobhunt_ai", name)
        for name in ("CandidateProfile", "AgentRun", "OfferLead")
    ]
    for run in (
        AgentRun.objects.using(alias)
        .filter(
            owner__isnull=True,
            application__isnull=False,
        )
        .select_related("application")
    ):
        run.owner_id = run.application.owner_id
        run.save(using=alias, update_fields=["owner"])
    orphans = [
        model.objects.using(alias).filter(owner__isnull=True) for model in models
    ]
    if any(queryset.exists() for queryset in orphans):
        owner = owner_for_orphans(apps)
        for queryset in orphans:
            queryset.update(owner=owner)
