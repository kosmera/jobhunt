"""Short-lived, account-bound links to stored files, served by the application.

The web side of ``StoragePort.get_secure_url`` for the providers whose bytes
the application streams itself (disk, memory): a token signed with
``SECRET_KEY`` naming the file and the account found in its path
(``documents/<owner_id>/…``), valid ``link_ttl`` seconds, read back by the
``tracker:private_file`` view — which also checks that the signed-in account
is the one named. The token format belongs here, to the views and URLs, not
to the storage adapters; the Azure adapter answers with a SAS URL instead.
"""

from __future__ import annotations

from django.conf import settings
from django.core import signing
from django.urls import reverse

from tracker.ports import FILES_PREFIX

#: Seconds a link stays valid when the configuration says nothing.
DEFAULT_LINK_TTL = 300

SALT = "tracker.links"


class BadLink(ValueError):
    """A token that is not ours, was altered, or has expired."""


def owner_id_of(file_name: str) -> int | None:
    """The account in ``documents/<owner_id>/…``; ``None`` for another layout."""
    parts = file_name.split("/")
    if len(parts) >= 3 and parts[0] + "/" == FILES_PREFIX and parts[1].isdigit():
        return int(parts[1])
    return None


def make_link(file_name: str) -> str:
    """The signed, account-bound URL of ``tracker:private_file`` for that file."""
    payload = {"n": file_name, "u": owner_id_of(file_name)}
    return reverse("tracker:private_file", args=[signing.dumps(payload, salt=SALT, compress=True)])


def read_link(token: str, *, max_age: int | None = None) -> tuple[str, int | None]:
    """``(file_name, owner_id)`` behind a token; ``BadLink`` when it is not
    ours, was altered or is older than ``max_age`` (the configured TTL)."""
    try:
        payload = signing.loads(token, salt=SALT, max_age=link_ttl() if max_age is None else max_age)
    except signing.BadSignature as exc:  # SignatureExpired is a BadSignature
        raise BadLink(str(exc)) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("n"), str):
        raise BadLink("Jeton sans nom de fichier.")
    owner_id = payload.get("u")
    return payload["n"], owner_id if isinstance(owner_id, int) else None


def link_ttl() -> int:
    """The configured validity of a link, in seconds (``STORAGES`` ``link_ttl``)."""
    options = settings.STORAGES.get("default", {}).get("OPTIONS", {})
    return int(options.get("link_ttl", DEFAULT_LINK_TTL))
