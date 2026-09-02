"""Where the use cases get their persistence from.

``persistence()`` builds the adapter named by ``settings.PERSISTENCE_ADAPTER``
once and hands the same instance out afterwards. This is a seam for tests
and extensions (swap in ``MemoryPersistence`` to run the rules without a
database) — NOT the production engine switch: SQLite and PostgreSQL are both
served by the ORM adapter and chosen through ``JOBHUNT_DATABASE_URL``.
"""

from __future__ import annotations

from django.conf import settings
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.module_loading import import_string

from tracker.ports import Persistence

DEFAULT_ADAPTER = "tracker.adapters.django_orm.DjangoPersistence"

_instance: Persistence | None = None


def persistence() -> Persistence:
    global _instance
    if _instance is None:
        path = getattr(settings, "PERSISTENCE_ADAPTER", DEFAULT_ADAPTER)
        _instance = import_string(path)()
    return _instance


@receiver(setting_changed)
def _forget_adapter(*, setting: str, **kwargs) -> None:
    # ``override_settings`` in a test must take effect on the next call, and
    # its exit must bring the configured adapter back.
    global _instance
    if setting == "PERSISTENCE_ADAPTER":
        _instance = None
