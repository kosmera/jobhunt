"""Projection des résultats du copilote dans les candidatures du cœur.

Importer une piste, garder le texte d'annonce récupéré, répercuter un rapport
de compatibilité et journaliser un CV généré : tout ce qui écrit dans les
tables du ``tracker`` à partir des modèles du copilote passe ici.
"""

import rls
from django.utils import timezone

from jobhunt_ai.models import LEAD_SUMMARY_PREFIX, MatchReport
from tracker.forms import company_for
from tracker.models import Application, EventKind, Platform, SkillGap, Status


def import_lead(user, lead) -> Application:
    """Create an application from the supplied AI lead; return it."""
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


def cache_posting_text(application, text) -> None:
    """Persist fetched offer text so it is only looked up once."""
    with rls.as_user(application.owner_id):
        application.posting_raw = text
        application.save(update_fields=["posting_raw", "updated_at"])


def apply_match_report(report) -> None:
    """Project a completed AI report into the application and gap tables."""
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


def record_generated_cv(generated) -> None:
    """Journalise le CV généré sur la fiche de la candidature."""
    generated.application.log(
        EventKind.NOTE,
        f"CV généré par le copilote ({generated.language.upper()})",
    )
