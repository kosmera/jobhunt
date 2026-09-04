"""Where the use cases get their adapters from.

``persistence()`` builds the adapter named by ``settings.PERSISTENCE_ADAPTER``
once and hands the same instance out afterwards. This is a seam for tests
and extensions (swap in ``MemoryPersistence`` to run the rules without a
database) — NOT the production engine switch: SQLite and PostgreSQL are both
served by the ORM adapter and chosen through ``JOBHUNT_DATABASE_URL``.

``storage()`` is the file provider: the Django storage that
``settings.STORAGES["default"]`` names — built by ``jobhunt.storage`` from
``JOBHUNT_STORAGE_PROVIDER`` — checked to speak ``StoragePort``. Django
already keeps one instance per process and forgets it when the setting is
overridden in a test.

``cv_analyzer()`` is the AI layer, if any: ``settings.CV_ANALYZER`` (tests)
or the ``cv_analyzer`` attribute of an installed extension, a dotted path
to a class taking no argument; ``None`` when nobody analyses CVs.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import storages
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.module_loading import import_string

from jobhunt.plugins import plugin_attribute
from tracker.ports import CVAnalyzer, Persistence, StoragePort

DEFAULT_ADAPTER = "tracker.adapters.django_orm.DjangoPersistence"

_instance: Persistence | None = None


def persistence() -> Persistence:
    global _instance
    instance = _instance
    if instance is None:
        path = getattr(settings, "PERSISTENCE_ADAPTER", DEFAULT_ADAPTER)
        instance = _instance = import_string(path)()
    return instance


_analyzer: CVAnalyzer | None = None
_analyzer_resolved = False


@receiver(setting_changed)
def _forget_adapter(*, setting: str, **kwargs) -> None:
    # ``override_settings`` in a test must take effect on the next call, and
    # its exit must bring the configured adapter back.
    global _instance, _analyzer, _analyzer_resolved
    if setting == "PERSISTENCE_ADAPTER":
        _instance = None
    if setting == "CV_ANALYZER":
        _analyzer, _analyzer_resolved = None, False


def storage() -> StoragePort:
    backend = storages["default"]
    if not isinstance(backend, StoragePort):
        raise ImproperlyConfigured(
            f"STORAGES['default'] ({backend.__class__.__name__}) ne parle pas le port de "
            "stockage ; vois jobhunt/storage.py."
        )
    return backend


def cv_analyzer() -> CVAnalyzer | None:
    global _analyzer, _analyzer_resolved
    if not _analyzer_resolved:
        path = getattr(settings, "CV_ANALYZER", None) or plugin_attribute("cv_analyzer")
        _analyzer = import_string(path)() if path else None
        _analyzer_resolved = True
    return _analyzer

