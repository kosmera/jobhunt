"""Le côté candidat de toute comparaison, et l'échelle commune de notation.

Deux agents notent des offres : la veille en pré-tri (un extrait d'annonce,
toutes les offres d'un coup) et l'évaluation en détail (l'annonce entière,
une offre à la fois). Tant que chacun portait sa propre définition du score,
la même offre pouvait passer de 88 à 65 au seul changement d'agent. Les deux
partagent donc désormais :

- :data:`SCORE_SCALE`, les repères de l'échelle, inclus mot pour mot dans les
  deux invites ;
- :func:`rubric_for`, une grille de qualifications dérivée une seule fois du
  profil puis rangée dessus — la veille compare à cette grille plutôt qu'à un
  résumé de CV, sans payer la sérialisation du profil complet à chaque offre.

Reste un écart irréductible : le pré-tri ne voit qu'un extrait. Il le dit par
le champ ``confidence`` au lieu de le maquiller dans la note.
"""

from __future__ import annotations

import json

import rls

from jobhunt_ai.agents.schemas import QualificationRubric
from jobhunt_ai.models import CandidateProfile
from jobhunt_ai.services import llm

#: Repères de l'échelle de compatibilité, communs au pré-tri et à
#: l'évaluation. Les seuils 80 et 65 sont ceux des pastilles de l'interface
#: (``OfferLead.score_band``) : un score et sa couleur disent la même chose.
SCORE_SCALE = """\
- le score reflète le recouvrement réel entre exigences et profil, pas la
  sympathie : 80 et plus « candidature évidente », 65 à 79 « ça vaut le
  coup », 50 à 64 « pari risqué », en dessous de 50 « hors cible » ;
- distingue exigences dures (bloquantes) et souhaits (secondaires) ; une
  exigence dure clairement non couverte plafonne le score à 55 ;
- tiens compte de la localisation et de la langue de l'offre ;
- note dans l'absolu, jamais par rapport aux autres offres examinées."""

RUBRIC_PROMPT = """\
Tu condenses les qualifications d'un candidat en une grille de tri courte,
qui servira à noter des offres vues en quelques lignes seulement. Sois
factuel et sévère : ne compte comme maîtrisée qu'une compétence que
l'expérience démontre, pas une compétence simplement citée. Ce que le CV ne
dit pas reste vide plutôt qu'inventé. Rédige en français."""


def profile_context(profile: CandidateProfile) -> str:
    """Le profil complet, condensé en JSON compact pour le prompt.

    L'identité (nom, e-mail, téléphone, point de départ) vient du compte, pas
    du CV : c'est elle qui remplit l'en-tête d'un CV généré, que le texte
    anonymisé remis à l'analyse ne pourrait plus fournir. Les champs vides —
    un compte sans téléphone — sortent du contexte plutôt que d'inviter le
    modèle à combler le trou.
    """
    payload = {
        "nom": profile.full_name,
        "titre": profile.headline,
        "email": profile.email,
        "telephone": profile.phone,
        "localisation": profile.location,
        "resume": profile.summary,
        "competences": profile.skills,
        "experiences": profile.experiences,
        "formation": profile.education,
        "langues": profile.languages,
        "certifications": profile.certifications,
    }
    return json.dumps(
        {key: value for key, value in payload.items() if value}, ensure_ascii=False
    )


def rubric_for(profile: CandidateProfile, run_id: int | None = None) -> dict:
    """La grille de qualifications du profil, dérivée à la première demande.

    Un appel par profil, pas un par offre : le coût est amorti sur toutes les
    veilles suivantes. L'analyse d'un nouveau CV crée un nouveau profil, donc
    une grille vide, donc une dérivation — rien à invalider à la main.
    """
    if profile.qualifications:
        return profile.qualifications

    result = llm.parse_structured(
        QualificationRubric,
        system=RUBRIC_PROMPT,
        content=(
            "Établis la grille de qualifications de ce candidat.\n\n"
            f"<profil>\n{profile_context(profile)}\n</profil>"
        ),
        run_id=run_id,
        owner_id=profile.owner_id,
    )
    profile.qualifications = result.data.model_dump()
    with rls.as_user(profile.owner_id):
        profile.save(update_fields=["qualifications", "updated_at"])
    return profile.qualifications


def rubric_text(rubric: dict) -> str:
    """La grille en quelques lignes lisibles, pour l'invite de pré-tri."""
    lines = [
        ("Séniorité", rubric.get("seniority", "")),
        ("Postes visés", ", ".join(rubric.get("target_roles") or [])),
        ("Maîtrisé", ", ".join(rubric.get("core_skills") or [])),
        ("Connu sans profondeur", ", ".join(rubric.get("secondary_skills") or [])),
        ("Langues", ", ".join(rubric.get("languages") or [])),
        ("Mobilité", rubric.get("mobility", "")),
        ("Rédhibitoire", ", ".join(rubric.get("deal_breakers") or [])),
    ]
    return "\n".join(f"{label} : {value}" for label, value in lines if value)
