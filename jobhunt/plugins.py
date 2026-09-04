"""Découverte des extensions hors dépôt.

Une extension est un paquet installé qui expose un descripteur via le point
d'entrée ``jobhunt.plugins`` : une app Django, un module d'URLs et, en
option, des entrées de navigation et des gabarits à injecter. Le cœur ne
connaît aucune extension par son nom — installer le paquet suffit, le
désinstaller retire tout.

Le descripteur est un objet à attributs simples :

- ``app`` : chemin pointé de l'AppConfig, ajouté à ``INSTALLED_APPS`` ;
- ``urls`` / ``url_prefix`` : module d'URLs et préfixe de montage ;
- ``nav_items`` : liste de tuples ``(route, libellé, icône, clé de badge)`` ;
- ``nav_badges`` : chemin pointé d'une fonction ``f(request)`` renvoyant
  ``{clé: nombre}`` — les compteurs sont ceux du profil connecté ;
- ``icon_templates`` : gabarits de sprites SVG inclus dans chaque page ;
- ``application_panels`` : gabarits injectés dans la fiche candidature ;
- ``cv_analyzer`` : chemin pointé d'une classe sans argument qui reçoit le
  texte anonymisé d'un CV téléversé (``tracker.ports.CVAnalyzer``).

Ce module est importé par ``settings.py`` : rien ici ne doit dépendre du
chargement des apps Django.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib.metadata import entry_points

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_plugins() -> tuple:
    plugins = []
    for entry in sorted(entry_points(group="jobhunt.plugins"), key=lambda e: e.name):
        try:
            plugins.append(entry.load())
        except Exception:
            # Une extension cassée ne doit pas empêcher l'outil de démarrer.
            logger.exception("Extension JobHunt illisible : %s", entry.name)
    return tuple(plugins)


def plugin_apps() -> list[str]:
    return [plugin.app for plugin in get_plugins()]


def plugin_nav_items() -> list[tuple]:
    items: list[tuple] = []
    for plugin in get_plugins():
        items.extend(getattr(plugin, "nav_items", []))
    return items


def plugin_nav_badges(request) -> dict[str, int]:
    from django.utils.module_loading import import_string

    badges: dict[str, int] = {}
    for plugin in get_plugins():
        path = getattr(plugin, "nav_badges", "")
        if not path:
            continue
        try:
            badges.update(import_string(path)(request))
        except Exception:
            logger.exception("Badges illisibles pour l'extension %s", plugin.name)
    return badges


def plugin_templates(attribute: str) -> list[str]:
    templates: list[str] = []
    for plugin in get_plugins():
        templates.extend(getattr(plugin, attribute, []))
    return templates


def plugin_attribute(name: str) -> str:
    """La valeur (chaîne) du descripteur qui définit ``name``, sinon ``""``.

    Un attribut de ce genre désigne *le* fournisseur d'un service (l'analyse
    de CV, par exemple) : deux extensions qui le définissent sont une erreur
    de configuration, pas un choix silencieux.
    """
    found = [(plugin.name, str(getattr(plugin, name))) for plugin in get_plugins() if getattr(plugin, name, "")]
    if len(found) > 1:
        from django.core.exceptions import ImproperlyConfigured

        names = ", ".join(plugin_name for plugin_name, _ in found)
        raise ImproperlyConfigured(f"Plusieurs extensions définissent « {name} » : {names}.")
    return found[0][1] if found else ""

