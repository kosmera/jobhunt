"""Lecture des sources d'offres et dédoublonnage des pistes.

Les sources se résument à « où chercher » (voir ``conf.scout_sources``) : la
partie fragile — comprendre la page — est déléguée au modèle, donc pas de
sélecteurs CSS à maintenir. Reste à obtenir la page, ce qui est devenu le
vrai point dur : les sites d'offres bloquent une requête ``httpx`` nue. Quand
un jeton Bright Data est configuré, la lecture passe par son serveur MCP, qui
déjoue les protections et rend directement du Markdown ; sinon on retombe sur
la lecture directe, qui marche encore sur les sites les plus ouverts.
"""

from __future__ import annotations

import logging
from urllib.parse import quote_plus, urljoin

from jobhunt_ai import conf
from jobhunt_ai.scraping import brightdata

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Macintosh) JobHunt-Copilote/0.1"


class ScrapeError(RuntimeError):
    """A source failure with a safe, actionable message for the run status."""


def _fill(template: str, source_name: str, **values: object) -> str:
    """Remplit un gabarit de source, en nommant la source si un emplacement
    est inconnu — sinon l'utilisateur ne récolte qu'un ``KeyError`` nu."""
    try:
        return template.format(**values)
    except (KeyError, IndexError) as exc:
        attendus = ", ".join(f"{{{key}}}" for key in values)
        raise RuntimeError(
            f"source « {source_name} » : gabarit invalide, emplacement "
            f"{exc} inconnu (attendus : {attendus})"
        ) from exc


def iter_search_pages(
    queries: list[str], location: str = "", radius_km: int | None = None, *, sources=None
):
    """Toutes les paires source × requête, dans l'ordre de configuration.

    Chaque élément décrit une lecture à faire : ``mode`` vaut ``"scrape"``
    (une page de résultats) ou ``"search"`` (une requête à un moteur), et
    ``label`` sert à situer la source dans les prompts et les avertissements.

    ``location`` et ``radius_km`` alimentent les emplacements du même nom :
    LinkedIn et Indeed veulent la zone dans l'URL, contrairement aux sites
    belges qui ne prennent que les mots-clés.
    """
    location = location or conf.DEFAULT_LOCATION
    radius = conf.DEFAULT_RADIUS_KM if radius_km is None else radius_km

    configured = conf.scout_sources() if sources is None else sources
    for query in queries:
        for source in configured:
            name = source["name"]
            search = source.get("search")
            if search:
                # Une recherche est une phrase, pas une URL : rien à encoder.
                terms = _fill(
                    search, name, query=query, location=location, radius=radius
                )
                engine = source.get("engine") or "google"
                yield {
                    "name": name,
                    "query": query,
                    "mode": "search",
                    "search": terms,
                    "engine": engine,
                    "geo_location": source.get("geo_location") or "",
                    # Pas de page d'origine : les résultats d'un moteur portent
                    # déjà des liens absolus.
                    "url": "",
                    "label": f"recherche {engine} « {terms} »",
                }
            else:
                url = _fill(
                    source["url"],
                    name,
                    query=quote_plus(query),
                    location=quote_plus(location),
                    radius=radius,
                )
                yield {
                    "name": name,
                    "query": query,
                    "mode": "scrape",
                    "url": url,
                    "label": url,
                }


def fetch_page(page: dict, char_limit: int | None = None) -> str:
    """Texte d'une source, quelle que soit sa forme (page ou recherche)."""
    if page.get("mode") == "search":
        return fetch_search_results(
            page["search"],
            engine=page.get("engine") or "google",
            geo_location=page.get("geo_location") or "",
            char_limit=char_limit,
        )
    return fetch_page_text(page["url"], char_limit)


def fetch_page_text(url: str, char_limit: int | None = None) -> str:
    """Rapatrie une page et la réduit à un texte exploitable par le modèle.

    Bright Data d'abord quand il est configuré. S'il échoue, on retente en
    direct — un blocage vaut mieux qu'une veille muette — sauf si
    ``JOBHUNT_AI_BRIGHTDATA_FALLBACK=0`` demande de voir l'erreur telle quelle.
    """
    if brightdata.is_configured():
        try:
            return _truncate(brightdata.scrape_as_markdown(url), char_limit)
        except brightdata.BrightDataError as exc:
            if not conf.BRIGHTDATA_FALLBACK:
                raise
            logger.warning(
                "Bright Data indisponible sur %s (%s) : lecture directe.", url, exc
            )
    return _truncate(_fetch_direct(url), char_limit)


def fetch_search_results(
    query: str,
    *,
    engine: str = "google",
    geo_location: str = "",
    char_limit: int | None = None,
) -> str:
    """Résultats d'un moteur de recherche : titre, URL et description.

    Sans intermédiaire capable de déjouer les protections, interroger un
    moteur depuis un serveur n'aboutit pas : cette forme de source demande
    Bright Data, et le dit plutôt que de laisser croire à une panne réseau.
    """
    if not brightdata.is_configured():
        raise RuntimeError(
            "source de type recherche : elle demande Bright Data "
            "(définis BRIGHTDATA_API_TOKEN)"
        )
    return _truncate(
        brightdata.search_engine(query, engine=engine, geo_location=geo_location),
        char_limit,
    )


def _truncate(text: str, char_limit: int | None = None) -> str:
    return text[: char_limit or conf.SCOUT_PAGE_CHAR_LIMIT]


def _fetch_direct(url: str) -> str:
    """Lecture sans intermédiaire, réduite au texte visible de la page."""
    import httpx
    from bs4 import BeautifulSoup

    response = httpx.get(
        url, timeout=25, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = response.status_code
        if status in (401, 403):
            message = (
                f"Lecture directe refusée (HTTP {status}). "
                "Vérifie la configuration Bright Data du worker."
            )
        elif status in (404, 410):
            message = (
                f"Page introuvable (HTTP {status}). "
                "Vérifie l'URL configurée pour cette source."
            )
        elif status == 429:
            message = "La source limite les requêtes (HTTP 429). Réessaie plus tard."
        else:
            message = f"La source a renvoyé une erreur HTTP {status}."
        raise ScrapeError(message) from exc
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(strip=True).casefold() if soup.title else ""
    if title in {"challenge validation", "just a moment...", "access denied"}:
        raise ScrapeError(
            "La source a renvoyé une page de vérification anti-robot. "
            "Vérifie la configuration Bright Data du worker."
        )
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    # Conserve les liens : le modèle en a besoin pour donner l'URL des offres.
    # (Bright Data rend du Markdown, où les liens sont déjà présents.)
    for anchor in soup.find_all("a", href=True):
        anchor.append(f" [{anchor['href']}]")

    return " ".join(soup.get_text(separator=" ").split())


def safe_http_url(url: str) -> str:
    """Ne laisse passer que http(s) : une URL produite par le modèle à partir
    d'une page tierce pourrait porter un schéma ``javascript:`` ou ``data:``
    et deviendrait un XSS stocké une fois rendue dans un ``href``."""
    url = (url or "").strip()
    if url.lower().startswith(("http://", "https://")):
        return url
    return ""


def absolutize(url: str, base_url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    return safe_http_url(urljoin(base_url, url))


def _key(title: str, company: str) -> tuple[str, str]:
    return title.strip().lower(), company.strip().lower()


def deduplicate(offers: list[dict], owner_id: int) -> list[dict]:
    """Écarte ce que le compte suit déjà (candidatures, pistes) et les doublons du lot."""
    from tracker.models import Application

    from jobhunt_ai.models import OfferLead

    leads = OfferLead.objects.filter(owner_id=owner_id)
    applications = list(
        Application.objects.filter(owner_id=owner_id).values_list(
            "url", "title", "company__name"
        )
    )
    known_urls = set(leads.exclude(url="").values_list("url", flat=True)) | set(
        url for url, _, _ in applications if url
    )
    known_pairs = {
        _key(title, company) for title, company in leads.values_list("title", "company_name")
    } | {_key(title, company) for _, title, company in applications}

    kept: list[dict] = []
    for offer in offers:
        title = (offer.get("title") or "").strip()
        if not title:
            continue
        url = (offer.get("url") or "").strip()
        pair = _key(title, offer.get("company_name") or "")
        if url and url in known_urls:
            continue
        if pair in known_pairs:
            continue
        if url:
            known_urls.add(url)
        known_pairs.add(pair)
        kept.append(offer)
    return kept
