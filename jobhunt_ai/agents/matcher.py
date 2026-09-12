"""Evaluate a profile against an application's offer text.

Detailed results stay in MatchReport; ``services.applications`` projects
them into the application and skill-gap tables inside the same transaction.
"""

from __future__ import annotations

from typing import TypedDict

import rls
from langgraph.graph import END, START, StateGraph

from jobhunt_ai import conf
from jobhunt_ai.agents.qualifications import SCORE_SCALE, profile_context
from jobhunt_ai.agents.schemas import MatchVerdict
from jobhunt_ai.models import (
    AgentRun,
    CandidateProfile,
    MatchReport,
)
from jobhunt_ai.services import llm
from jobhunt_ai.services.applications import apply_match_report, cache_posting_text
from tracker.models import Application

SYSTEM_PROMPT = f"""\
Tu évalues la compatibilité entre le profil d'un candidat et une offre
d'emploi, pour son propre outil de suivi. Sois honnête et calibré :
{SCORE_SCALE}
- rédige tout le texte libre en français, tutoiement, direct et concret."""

#: Longueur maximale du texte d'offre injecté dans le prompt.
OFFER_CHAR_LIMIT = 20000


class MatcherState(TypedDict, total=False):
    run_id: int
    owner_id: int
    application_id: int
    profile_id: int
    offer_text: str
    verdict: dict
    report_id: int
    score: int


def fetch_offer_text(url: str) -> str:
    """Rapatrie l'annonce depuis son lien quand le texte n'a pas été collé."""
    import httpx
    from bs4 import BeautifulSoup

    from jobhunt_ai.scraping.sources import safe_http_url

    if not safe_http_url(url):
        return ""
    response = httpx.get(
        url,
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (JobHunt Copilote)"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ").split())


def resolve_offer_text(application) -> str:
    """Le texte d'offre à donner au modèle : collé, sinon récupéré du lien
    (et conservé dans ``posting_raw`` pour ne le chercher qu'une fois),
    sinon le résumé. Trop maigre → erreur claire plutôt qu'analyse creuse."""
    offer_text = application.posting_raw.strip()
    if not offer_text and application.url:
        try:
            offer_text = fetch_offer_text(application.url)
        except Exception:
            offer_text = ""
        if len(offer_text) >= 80:
            cache_posting_text(application, offer_text)
    if not offer_text:
        offer_text = application.summary.strip()
    if len(offer_text) < 80:
        raise RuntimeError(
            "Pas assez de matière sur cette offre : colle le texte de l'annonce "
            "dans « Texte de l'annonce » ou renseigne un lien accessible."
        )
    header = (
        f"Poste : {application.title}\n"
        f"Société : {application.company.name}\n"
        f"Lieu : {application.location or 'non précisé'}"
    )
    return f"{header}\n\n{offer_text[:OFFER_CHAR_LIMIT]}"


def load_context(state: MatcherState) -> MatcherState:
    with rls.as_user(state["owner_id"]):
        application = Application.objects.select_related("company", "owner").get(
            owner_id=state["owner_id"], pk=state["application_id"]
        )
    return {"offer_text": resolve_offer_text(application)}


def evaluate(state: MatcherState) -> MatcherState:
    with rls.as_user(state["owner_id"]):
        profile = CandidateProfile.objects.get(
            pk=state["profile_id"], owner_id=state["owner_id"]
        )
        content = (
            "Évalue la compatibilité entre ce profil et cette offre.\n\n"
            f"<profil>\n{profile_context(profile)}\n</profil>\n\n"
            f"<offre>\n{state['offer_text']}\n</offre>"
        )
    result = llm.parse_structured(
        MatchVerdict,
        system=SYSTEM_PROMPT,
        content=content,
        run_id=state.get("run_id"),
        owner_id=state["owner_id"],
    )
    return {"verdict": result.data.model_dump()}


def persist(state: MatcherState) -> MatcherState:
    # Rien que de la base ici : un seul bloc, donc une seule transaction.
    with rls.as_user(state["owner_id"]):
        verdict = MatchVerdict.model_validate(state["verdict"])
        application = Application.objects.select_related("company", "owner").get(
            owner_id=state["owner_id"], pk=state["application_id"]
        )
        profile = CandidateProfile.objects.filter(
            pk=state["profile_id"], owner_id=state["owner_id"]
        ).first()

        report = MatchReport.objects.create(
            application=application,
            profile=profile,
            run=AgentRun.objects.filter(
                pk=state.get("run_id"),
                owner_id=state["owner_id"],
            ).first(),
            score=verdict.score,
            summary=verdict.summary,
            strengths=verdict.strengths,
            weaknesses=verdict.weaknesses,
            strategy=verdict.strategy,
            matched_skills=verdict.matched_skills,
            partial_skills=verdict.partial_skills,
            missing_skills=verdict.missing_skills,
            gaps=[gap.model_dump() for gap in verdict.gaps],
            model_id=conf.MODEL_ID,
        )

        # Même transaction que le rapport : la fiche et le verdict vont ensemble.
        apply_match_report(report)
        return {"report_id": report.pk, "score": verdict.score}


def build_graph():
    graph = StateGraph(MatcherState)
    graph.add_node("load_context", load_context)
    graph.add_node("evaluate", evaluate)
    graph.add_node("persist", persist)
    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "evaluate")
    graph.add_edge("evaluate", "persist")
    graph.add_edge("persist", END)
    return graph.compile()


_graph = None


def resolve_profile(agent_run: AgentRun) -> CandidateProfile:
    """Le profil candidat du compte : celui demandé, sinon le principal."""
    profile_id = agent_run.params.get("profile_id")
    profile = (
        CandidateProfile.objects.filter(
            pk=profile_id, owner_id=agent_run.owner_id
        ).first()
        if profile_id
        else CandidateProfile.primary(agent_run.owner_id)
    )
    if profile is None:
        raise RuntimeError("Aucun profil candidat : analyse d'abord un CV.")
    return profile


def resolve_application_id(agent_run: AgentRun) -> int:
    """Les agents d'évaluation et de génération travaillent sur une
    candidature ; le champ est facultatif sur ``AgentRun`` (la veille n'en a
    pas), d'où ce garde-fou."""
    if agent_run.application_id is None:
        raise RuntimeError("Cette exécution n'est rattachée à aucune candidature.")
    return agent_run.application_id


def run(agent_run: AgentRun) -> dict:
    global _graph
    if _graph is None:
        _graph = build_graph()
    with rls.as_user(agent_run.owner_id):
        profile = resolve_profile(agent_run)
        if agent_run.profile_id != profile.pk:
            agent_run.profile = profile
            agent_run.save(update_fields=["profile"])
    state = _graph.invoke(
        {
            "run_id": agent_run.pk,
            "owner_id": agent_run.owner_id,
            "application_id": resolve_application_id(agent_run),
            "profile_id": profile.pk,
        }
    )
    return {"report_id": state["report_id"], "score": state["score"]}
