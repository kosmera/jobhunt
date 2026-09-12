"""Agent de génération : profil + offre → CV ATS ciblé, rangé avec la candidature.

Le modèle rédige le contenu (structure ATSResume), le rendu DOCX est fait en
local par ``services.ats`` : mise en page déterministe, contenu vérifiable.
"""

from __future__ import annotations

from typing import TypedDict

import rls
from langgraph.graph import END, START, StateGraph

from jobhunt_ai import conf
from jobhunt_ai.agents.matcher import (
    resolve_application_id,
    resolve_offer_text,
    resolve_profile,
)
from jobhunt_ai.agents.qualifications import profile_context
from jobhunt_ai.agents.schemas import ATSResume
from jobhunt_ai.models import AgentRun, CandidateProfile, GeneratedCV, MatchReport
from jobhunt_ai.services import ats, llm
from jobhunt_ai.services.applications import record_generated_cv
from jobhunt_ai.services.documents import generated_document
from tracker.models import Application

SYSTEM_PROMPT = """\
Tu rédiges un CV ciblé sur une offre précise, pour le candidat dont le profil
t'est fourni. Contraintes :
- contenu strictement véridique : reformule et hiérarchise le profil, mais
  n'invente ni expérience, ni chiffre, ni compétence ;
- reprends naturellement les mots-clés de l'offre là où le profil les couvre
  (les analyseurs ATS cherchent des correspondances littérales) ;
- des puces courtes, orientées résultats, verbe d'action en tête ;
- rédige dans la langue demandée ;
- mets en avant ce que l'offre demande, résume le reste."""

LANGUAGE_NAMES = {"fr": "français", "en": "anglais", "nl": "néerlandais"}


class GeneratorState(TypedDict, total=False):
    run_id: int
    owner_id: int
    application_id: int
    profile_id: int
    language: str
    offer_text: str
    resume: dict
    generated_id: int
    document_id: int


def load_context(state: GeneratorState) -> GeneratorState:
    with rls.as_user(state["owner_id"]):
        application = Application.objects.select_related("company", "owner").get(
            owner_id=state["owner_id"], pk=state["application_id"]
        )
    return {"offer_text": resolve_offer_text(application)}


def draft(state: GeneratorState) -> GeneratorState:
    language = LANGUAGE_NAMES.get(state.get("language", "fr"), "français")
    with rls.as_user(state["owner_id"]):
        profile = CandidateProfile.objects.get(
            pk=state["profile_id"], owner_id=state["owner_id"]
        )
        report = (
            MatchReport.objects.filter(
                application_id=state["application_id"],
                application__owner_id=state["owner_id"],
            )
            .order_by("-created_at")
            .first()
        )
        guidance = ""
        if report:
            guidance = (
                "\n\n<evaluation>\nPoints forts à mettre en avant :\n"
                f"{report.strengths}\n\nPoints faibles à ne pas souligner :\n"
                f"{report.weaknesses}\n</evaluation>"
            )
        content = (
            f"Rédige le CV en {language} pour cette offre.\n\n"
            f"<profil>\n{profile_context(profile)}\n</profil>\n\n"
            f"<offre>\n{state['offer_text']}\n</offre>"
            f"{guidance}"
        )
    result = llm.parse_structured(
        ATSResume,
        system=SYSTEM_PROMPT,
        content=content,
        run_id=state.get("run_id"),
        owner_id=state["owner_id"],
    )
    return {"resume": result.data.model_dump()}


def render_and_persist(state: GeneratorState) -> GeneratorState:
    """Render bytes locally, then file the document transactionally."""
    resume = ATSResume.model_validate(state["resume"])
    with rls.as_user(state["owner_id"]):
        application = Application.objects.select_related("company", "owner").get(
            owner_id=state["owner_id"], pk=state["application_id"]
        )
    language = state.get("language", "fr")
    payload = ats.render_docx(resume, language=language)
    with generated_document(application, payload, language) as document:
        generated = GeneratedCV.objects.create(
            application=application,
            profile=CandidateProfile.objects.filter(
                pk=state["profile_id"],
                owner_id=state["owner_id"],
            ).first(),
            run=AgentRun.objects.filter(
                pk=state.get("run_id"),
                owner_id=state["owner_id"],
            ).first(),
            document=document,
            language=language,
            content=state["resume"],
            model_id=conf.MODEL_ID,
        )
        record_generated_cv(generated)
    return {"generated_id": generated.pk, "document_id": document.pk}


def build_graph():
    graph = StateGraph(GeneratorState)
    graph.add_node("load_context", load_context)
    graph.add_node("draft", draft)
    graph.add_node("render_and_persist", render_and_persist)
    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "draft")
    graph.add_edge("draft", "render_and_persist")
    graph.add_edge("render_and_persist", END)
    return graph.compile()


_graph = None


def run(agent_run: AgentRun) -> dict:
    global _graph
    if _graph is None:
        _graph = build_graph()
    with rls.as_user(agent_run.owner_id):
        profile = resolve_profile(agent_run)
        if agent_run.profile_id != profile.pk:
            agent_run.profile = profile
            agent_run.save(update_fields=["profile"])
        language = agent_run.params.get("language") or (
            agent_run.application.cv_language if agent_run.application else "fr"
        )
    state = _graph.invoke(
        {
            "run_id": agent_run.pk,
            "owner_id": agent_run.owner_id,
            "application_id": resolve_application_id(agent_run),
            "profile_id": profile.pk,
            "language": language,
        }
    )
    return {
        "generated_id": state["generated_id"],
        "document_id": state["document_id"],
        "language": language,
    }
