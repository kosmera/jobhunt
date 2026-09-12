"""Parse anonymized CV text into a candidate profile.

The core's storage port extracts and anonymizes both text and labels. Account
identity comes from the profile; raw uploaded files never reach this agent or
the AI provider.
"""

from __future__ import annotations

from typing import TypedDict

import rls
from langgraph.graph import END, START, StateGraph

from jobhunt_ai.agents.schemas import ParsedProfile
from jobhunt_ai.models import AgentRun, CandidateProfile
from jobhunt_ai.services import documents, llm
from jobhunt_ai.services.accounts import identity as account_identity

SYSTEM_PROMPT = """\
Tu analyses le CV d'un candidat pour le compte de son propre outil de suivi
de candidatures. Extrais fidèlement le contenu vers le schéma demandé :
- ne rien inventer ni embellir ; un champ inconnu reste vide ;
- conserver la terminologie technique telle quelle (noms d'outils, sigles) ;
- rédiger les textes libres (résumé, descriptions) en français, même si le
  CV est dans une autre langue ;
- classer les expériences de la plus récente à la plus ancienne ;
- le CV a été anonymisé avant de t'être remis : des marqueurs entre crochets
  ([NOM], [EMAIL], [TELEPHONE], [LIEU], [URL], [ADRESSE]…) remplacent les
  identifiants du candidat. Ne les recopie jamais dans ta réponse — écris la
  phrase sans eux, ou laisse le champ vide."""


class ParserState(TypedDict, total=False):
    run_id: int
    owner_id: int
    document_id: int | None
    label: str
    language: str
    make_primary: bool
    raw_text: str
    profile_id: int
    skill_count: int


def load_text(state: ParserState) -> ParserState:
    """Le texte anonymisé arrive avec l'exécution ; il n'y a rien à ouvrir.

    Le cœur applique déjà le même seuil avant d'appeler l'analyseur : ce
    contrôle couvre les exécutions créées autrement (l'admin, une relance).
    """
    text = (state.get("raw_text") or "").strip()
    if len(text) < documents.MIN_TEXT_LENGTH:
        raise RuntimeError(
            "Le document ne contient presque pas de texte exploitable : "
            "vérifie qu'il s'agit bien d'un CV (un scan sans couche texte "
            "doit d'abord passer par une reconnaissance de caractères)."
        )
    return {"raw_text": text}


def parse(state: ParserState) -> ParserState:
    result = llm.parse_structured(
        ParsedProfile,
        system=SYSTEM_PROMPT,
        content=f"Analyse ce CV et remplis le schéma.\n\n<cv>\n{state['raw_text']}\n</cv>",
        run_id=state.get("run_id"),
        owner_id=state["owner_id"],
    )
    profile: ParsedProfile = result.data

    from django.contrib.auth import get_user_model

    owner_id = state["owner_id"]
    document_id = state.get("document_id")
    with rls.as_user(owner_id):
        owner = get_user_model().objects.get(pk=owner_id)
        identity = account_identity(owner)
        document = (
            documents.cv_documents(owner_id).filter(pk=document_id).first()
            if document_id
            else None
        )
        record = CandidateProfile.objects.create(
            owner_id=owner_id,
            label=state.get("label") or (document.label if document else "Profil"),
            language=profile.detected_language[:2] or state.get("language", ""),
            is_primary=state.get("make_primary", False)
            or not CandidateProfile.objects.filter(owner_id=owner_id).exists(),
            # L'identité vient du compte : le CV remis au modèle est
            # anonymisé, il n'en resterait que des marqueurs.
            full_name=identity.full_name,
            email=identity.email,
            phone=identity.phone,
            location=identity.location,
            headline=profile.headline,
            summary=profile.summary,
            skills=[skill.model_dump() for skill in profile.skills],
            experiences=[exp.model_dump() for exp in profile.experiences],
            education=[edu.model_dump() for edu in profile.education],
            languages=[lang.model_dump() for lang in profile.languages],
            certifications=[cert.model_dump() for cert in profile.certifications],
            raw_text=state.get("raw_text", ""),
            source_document=document,
        )
    return {"profile_id": record.pk, "skill_count": len(profile.skills)}


def build_graph():
    graph = StateGraph(ParserState)
    graph.add_node("load_text", load_text)
    graph.add_node("parse", parse)
    graph.add_edge(START, "load_text")
    graph.add_edge("load_text", "parse")
    graph.add_edge("parse", END)
    return graph.compile()


_graph = None


def run(agent_run: AgentRun) -> dict:
    global _graph
    if _graph is None:
        _graph = build_graph()
    params = agent_run.params
    state = _graph.invoke(
        {
            "run_id": agent_run.pk,
            "owner_id": agent_run.owner_id,
            "document_id": params.get("document_id"),
            "label": params.get("label", ""),
            "language": params.get("language", ""),
            "make_primary": params.get("make_primary", False),
            # Posé par l'analyseur (``hooks.CopilotCVAnalyzer``) à partir de
            # ce que le port de stockage a extrait puis anonymisé.
            "raw_text": params.get("text", ""),
        }
    )
    with rls.as_user(agent_run.owner_id):
        profile = CandidateProfile.objects.get(
            pk=state["profile_id"],
            owner_id=agent_run.owner_id,
        )
        agent_run.profile = profile
        agent_run.save(update_fields=["profile"])
    return {"profile_id": profile.pk, "skill_count": state.get("skill_count", 0)}
