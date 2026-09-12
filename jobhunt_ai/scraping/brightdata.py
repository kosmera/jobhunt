"""Accès à Bright Data par son serveur MCP distant.

``httpx`` + BeautifulSoup se font refuser par la plupart des sites d'offres :
détection de robots, rendu JavaScript, CAPTCHA. Bright Data expose un « web
unlocker » derrière un serveur MCP : on lui donne une URL, il rend la page en
Markdown — déjà débloquée, déjà nettoyée, donc plus de soupe HTML à démêler
avant de la donner au modèle.

Deux outils suffisent à la veille, et le point d'accès distant les expose
toujours : ``scrape_as_markdown`` pour une page, ``search_engine`` pour une
recherche Google/Bing/Yandex. :func:`get_tools` rend en plus l'ensemble des
outils du serveur sous forme d'outils LangChain, pour un agent qui les
choisirait lui-même.

Le protocole MCP est asynchrone alors que les agents tournent dans un thread
synchrone (``services/runner.py``) : ce module fait le pont et n'expose que
des fonctions synchrones.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from concurrent import futures
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobhunt_ai import conf

if TYPE_CHECKING:  # import différé : le paquet n'est tiré qu'à l'appel
    from langchain_mcp_adapters.sessions import StreamableHttpConnection

#: Nom logique du serveur dans le client MCP (un seul serveur ici).
SERVER_NAME = "brightdata"


class BrightDataError(RuntimeError):
    """Échec d'un appel à Bright Data, avec un message montrable dans l'interface."""


def is_configured() -> bool:
    """Vrai si un jeton est disponible. Sans jeton la veille reste possible,
    mais retombe sur la lecture directe — donc sur les blocages."""
    return bool(conf.BRIGHTDATA_API_TOKEN)


def _redact(message: str) -> str:
    """Le jeton voyage dans l'URL : il ne doit apparaître ni dans un journal,
    ni dans le champ ``error`` d'une exécution, que l'interface affiche."""
    token = conf.BRIGHTDATA_API_TOKEN
    return message.replace(token, "***") if token and token in message else message


def _endpoint() -> str:
    """URL du serveur MCP, jeton ajouté aux paramètres déjà présents.

    Bright Data authentifie par ``?token=`` (pas d'en-tête ``Authorization``),
    et c'est aussi par la requête qu'on choisit les outils exposés :
    ``…/mcp?groups=browser`` ou ``…/mcp?tools=extract``. On préserve donc ce
    qui est configuré et on n'ajoute que le jeton.
    """
    token = conf.BRIGHTDATA_API_TOKEN
    if not token:
        raise BrightDataError(
            "Bright Data n'est pas configuré : définis BRIGHTDATA_API_TOKEN."
        )
    parts = urlsplit(conf.BRIGHTDATA_MCP_URL)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "token"]
    query.append(("token", token))
    return urlunsplit(parts._replace(query=urlencode(query)))


def _connection() -> StreamableHttpConnection:
    return {
        "transport": "streamable_http",
        "url": _endpoint(),
        "timeout": conf.BRIGHTDATA_TIMEOUT,
        # Débloquer une page peut demander plusieurs dizaines de secondes :
        # le flux reste silencieux pendant ce temps.
        "sse_read_timeout": conf.BRIGHTDATA_TIMEOUT,
    }


def _run_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    """Exécute une coroutine depuis du code synchrone.

    Les agents tournent dans un thread sans boucle d'événements : ``asyncio.run``
    suffit. Sous ASGI une boucle tourne déjà dans ce thread — on bascule alors
    sur un thread dédié plutôt que d'échouer.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _describe(exc: BaseException, depth: int = 0) -> str:
    """Message lisible d'une exception, y compris à travers les groupes.

    Le client MCP travaille sous ``anyio`` : un jeton refusé ou une panne
    réseau remonte emballé dans un ``ExceptionGroup`` dont le ``str`` ne dit
    rien d'autre que « unhandled errors in a TaskGroup ». On va chercher la
    vraie cause, sinon l'erreur affichée sur l'exécution est inexploitable.
    """
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions and depth < 5:
        # dict.fromkeys : dédoublonne en gardant l'ordre.
        causes = dict.fromkeys(_describe(sub, depth + 1) for sub in exc.exceptions)
        return " ; ".join(causes)
    return str(exc).strip() or exc.__class__.__name__


def _result_text(result: Any) -> str:
    """Concatène les blocs texte d'un ``CallToolResult`` (on ignore images et
    ressources : les outils utilisés ici ne renvoient que du texte)."""
    blocks = [
        block.text
        for block in getattr(result, "content", [])
        if getattr(block, "type", "") == "text"
    ]
    return "\n".join(blocks).strip()


async def _acall_tool(name: str, arguments: dict[str, Any]) -> str:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient({SERVER_NAME: _connection()})
    # Une session par appel : la veille lit quelques pages par exécution, le
    # coût d'établissement est négligeable devant celui du déblocage, et rien
    # ne survit à l'appel — donc pas de session à recycler entre deux threads.
    async with client.session(SERVER_NAME) as session:
        result = await session.call_tool(name, arguments)

    text = _result_text(result)
    if getattr(result, "isError", False):
        raise BrightDataError(
            _redact(text) or f"Bright Data a rejeté l'appel « {name} »."
        )
    if not text:
        raise BrightDataError(f"Bright Data n'a rien renvoyé pour « {name} ».")
    return text


def call_tool(name: str, arguments: dict[str, Any]) -> str:
    """Appelle un outil du serveur MCP et renvoie sa sortie texte."""
    try:
        return _run_sync(_acall_tool(name, arguments))
    except BrightDataError:
        raise
    except ImportError as exc:  # venv désynchronisé du lock
        raise BrightDataError(
            "Le paquet langchain-mcp-adapters est absent : relance « uv sync »."
        ) from exc
    except Exception as exc:
        detail = _redact(_describe(exc))
        raise BrightDataError(f"Appel Bright Data « {name} » impossible : {detail}") from exc


def scrape_as_markdown(url: str) -> str:
    """Rend une page en Markdown, protections anti-robot déjouées."""
    return call_tool("scrape_as_markdown", {"url": url})


def search_engine(query: str, *, engine: str = "google", geo_location: str = "") -> str:
    """Résultats d'un moteur de recherche : titre, URL et description."""
    arguments: dict[str, Any] = {"query": query, "engine": engine}
    if geo_location:
        arguments["geo_location"] = geo_location
    return call_tool("search_engine", arguments)


def get_tools() -> list:
    """Tous les outils du serveur, sous forme d'outils LangChain.

    La veille n'en a pas besoin : elle appelle directement ce qu'elle veut,
    ce qui garde le graphe déterministe et testable. C'est la porte d'entrée
    pour un agent qui choisirait ses outils lui-même, par exemple
    ``create_react_agent(model, brightdata.get_tools())`` — les outils rendus
    sont asynchrones, donc à invoquer depuis un graphe asynchrone.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient({SERVER_NAME: _connection()})
    try:
        return _run_sync(client.get_tools(server_name=SERVER_NAME))
    except Exception as exc:
        detail = _redact(_describe(exc))
        raise BrightDataError(f"Outils Bright Data indisponibles : {detail}") from exc
