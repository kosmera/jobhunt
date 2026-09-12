"""Scout preparation and single-target analysis; no run-wide network loops."""

from __future__ import annotations

from itertools import islice, product

import rls

from jobhunt_ai import conf
from jobhunt_ai.agents import qualifications
from jobhunt_ai.agents.qualifications import SCORE_SCALE
from jobhunt_ai.agents.schemas import LeadScores, ScoutQueries, ScrapedOffers
from jobhunt_ai.models import AgentRun, CandidateProfile
from jobhunt_ai.services import llm, runner
from jobhunt_ai.services.accounts import search_preferences
from jobhunt_ai.scraping import sources as scraping

QUERY_PROMPT = """\
Tu prépares des requêtes de recherche d'emploi pour des sites d'offres belges,
à partir du profil d'un candidat. Des requêtes courtes (2-4 mots), comme les
taperait un recruteur : intitulés de poste et technologies phares, pas de
phrases. Mélange français et anglais si le marché le fait."""

EXTRACT_PROMPT = """\
Tu lis une page de résultats d'un site d'offres d'emploi ou d'un moteur de
recherche, en texte brut ou en Markdown. Extrais uniquement les offres
réellement listées (pas les liens de navigation, pas les publicités). Les
liens doivent être absolus ; laisse l'URL vide si la page n'en montre pas
pour une offre."""

SCORE_PROMPT = f"""\
Tu fais le pré-tri d'offres repérées pour un candidat : sa grille de
qualifications d'un côté, un extrait d'annonce de l'autre. Pour chacune,
donne une compatibilité de 0 à 100 et une justification d'une phrase en
français.
{SCORE_SCALE}

Tu ne vois qu'un extrait, jamais l'annonce entière : dis-le par la fiabilité
plutôt qu'en gonflant ou en cassant la note. Une exigence dure dont l'extrait
ne parle pas n'est ni acquise ni disqualifiante — elle abaisse la fiabilité.
Sois sévère sur la distance : au-delà du rayon indiqué sans télétravail, le
score plonge."""


def _profile_summary(profile: CandidateProfile) -> str:
    skills = ", ".join(profile.skill_names[:25])
    return (
        f"Titre : {profile.headline or '?'}\n"
        f"Localisation : {profile.location or '?'}\n"
        f"Résumé : {profile.summary}\n"
        f"Compétences : {skills}"
    )


def build_queries(state: dict) -> dict[str, list[str]]:
    runner.progress(state.get("run_id"), state["owner_id"], "Préparation de la recherche")
    keywords = (state.get("keywords") or "").strip()
    if keywords:
        return {"queries": [keywords]}

    with rls.as_user(state["owner_id"]):
        profile = CandidateProfile.objects.get(pk=state["profile_id"], owner_id=state["owner_id"])
        content = (
            f"Propose {conf.SCOUT_MAX_QUERIES} requêtes pour ce profil.\n\n"
            f"<profil>\n{_profile_summary(profile)}\n</profil>"
        )
    result = llm.parse_structured(
        ScoutQueries,
        system=QUERY_PROMPT,
        content=content,
        run_id=state.get("run_id"),
        owner_id=state["owner_id"],
    )
    queries = [q.strip() for q in result.data.queries if q.strip()]
    if not queries:
        raise RuntimeError("Impossible de dériver des requêtes de recherche du profil.")
    return {"queries": queries[: conf.SCOUT_MAX_QUERIES]}


def prepare(agent_run: AgentRun) -> tuple[dict, list[dict]]:
    from jobhunt_ai.agents.matcher import resolve_profile

    with rls.as_user(agent_run.owner_id):
        profile = (
            CandidateProfile.objects.get(pk=agent_run.profile_id, owner_id=agent_run.owner_id)
            if agent_run.profile_id else resolve_profile(agent_run)
        )
        location, radius = search_preferences(agent_run.owner)
        state = {
            "run_id": agent_run.pk,
            "owner_id": agent_run.owner_id,
            "profile_id": profile.pk,
            "location": agent_run.params.get("location")
            or location or conf.DEFAULT_LOCATION,
            "radius_km": agent_run.params.get("radius_km")
            or radius,
            "keywords": agent_run.params.get("keywords", ""),
        }
    queries = build_queries(state)["queries"]
    state["queries"] = queries
    sources = []
    for query, source in islice(product(queries, conf.scout_sources()), conf.SCOUT_MAX_PAGES):
        try:
            page = next(scraping.iter_search_pages(
                [query], state["location"], state["radius_km"], sources=[source],
            ))
        except (AttributeError, KeyError, IndexError, TypeError, ValueError, RuntimeError):
            # Format failures belong to this target, not the whole manifest.
            page = {"name": source["name"], "query": query, "configuration_error": True}
        sources.append(page)
    if not sources:
        raise RuntimeError("Aucune source configurée.")
    # Freeze candidate context once; parallel targets neither race to derive
    # the rubric nor silently switch CVs midway through a run.
    state["rubric"] = qualifications.rubric_for(profile, agent_run.pk)
    return state, sources


def analyze_page(source: dict, text: str, state: dict) -> list[dict]:
    extracted = llm.parse_structured(
        ScrapedOffers,
        system=EXTRACT_PROMPT,
        content=(
            f"Source : {source['name']} — résultats pour « {source['query']} »\n"
            f"Origine : {source.get('label') or source.get('url', '')}\n\n"
            f"<page>\n{text}\n</page>"
        ),
        run_id=state["run_id"], owner_id=state["owner_id"],
    )
    offers = []
    for offer in extracted.data.offers:
        record = offer.model_dump()
        record["source_name"] = source["name"]
        record["url"] = scraping.absolutize(record.get("url", ""), source.get("url", ""))
        offers.append(record)
    with rls.as_user(state["owner_id"]):
        offers = scraping.deduplicate(offers, state["owner_id"])
    if not offers:
        return []
    listing = "\n\n".join(
        f"[{index}] {offer['title']} — {offer.get('company_name', '?')} "
        f"({offer.get('location', '?')})\n{offer.get('description', '')[:600]}"
        for index, offer in enumerate(offers)
    )
    result = llm.parse_structured(
        LeadScores, system=SCORE_PROMPT,
        content=(
            f"Zone du candidat : {state['location']}, rayon {state['radius_km']} km.\n\n"
            f"<qualifications>\n{qualifications.rubric_text(state['rubric'])}\n</qualifications>\n\n"
            f"<offres>\n{listing}\n</offres>"
        ),
        run_id=state["run_id"], owner_id=state["owner_id"],
    )
    scores = {score.index: score for score in result.data.scores}
    leads = []
    for index, offer in enumerate(offers):
        scored = scores.get(index)
        leads.append({
            "title": offer["title"][:250],
            "company_name": (offer.get("company_name") or "?")[:200],
            "location": (offer.get("location") or "")[:200],
            "url": offer.get("url", "")[:500],
            "source_name": source["name"][:100],
            "description": offer.get("description", ""),
            "language": (offer.get("language") or "")[:2],
            "score": scored.score if scored else None,
            "score_reason": scored.reason if scored else "",
            "score_confidence": scored.confidence[:20] if scored else "",
            "score_blockers": scored.blockers if scored else [],
        })
    return leads


def run(agent_run: AgentRun) -> None:
    from jobhunt_ai.services.fanout import dispatch

    context, sources = prepare(agent_run)
    dispatch(agent_run.pk, agent_run.owner_id, context, sources)
