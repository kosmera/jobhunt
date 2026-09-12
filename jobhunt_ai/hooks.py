"""Points d'accroche consommés par le cœur JobHunt (navigation, badges, CV).

Rien ici ne doit toucher l'ORM à l'import : ``tracker.context_processors``
importe ce module à la requête, quand le copilote est installé.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tracker.privacy import AnonymizedText

    from jobhunt_ai.models import AgentRun

#: Entrée ajoutée à la navigation : (route, libellé, icône, clé de badge).
NAV_ITEM = ("jobhunt_ai:copilot", "Copilote", "sparkle", "ai_leads")


def nav_badges(request) -> dict[str, int]:
    """Badges de navigation : le nombre de pistes encore à trier."""
    from jobhunt_ai.access import has_copilot_access
    from jobhunt_ai.models import LeadStatus, OfferLead

    if not has_copilot_access(request.user):
        return {}
    return {
        "ai_leads": OfferLead.objects.filter(
            owner=request.user, status=LeadStatus.NEW
        ).count()
    }


class CopilotCVAnalyzer:
    """Analyseur de CV pour les vues du copilote.

    Le cœur publie ``tracker.events.cv_ingested`` ; les vues, elles, appellent
    le récepteur directement pour récupérer l'exécution durable qu'il crée.
    """

    def __init__(self, *, make_primary: bool = False) -> None:
        self.make_primary = make_primary
        self.run: AgentRun | None = None

    def analyze_cv(
        self,
        owner,
        *,
        document_id: int,
        label: str,
        language: str,
        text: AnonymizedText,
    ) -> None:
        from jobhunt_ai.signals import receive_cv

        self.run = receive_cv(
            type(self),
            owner=owner,
            document_id=document_id,
            label=label,
            language=language,
            text=text,
            make_primary=self.make_primary,
        )
