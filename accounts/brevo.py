"""Minimal Brevo contact sync for the launch-interest worker.

The account's custom attributes, blocklists and existing identifiers stay owned
by Brevo. Only the opted-in email and optional launch-list membership are sent.
"""

from dataclasses import dataclass
from urllib.parse import quote

import httpx
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email


class BrevoError(RuntimeError):
    """A safe-to-store failure code, without response bodies or contact data."""

    def __init__(self, code: str, *, retryable: bool):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True)
class ContactSyncResult:
    contact_id: int | None
    # Known marketing/list suppression; Brevo SMTP enforces its own blocklist.
    email_blocked: bool


def _request(client, method, path, **kwargs):
    try:
        return client.request(method, path, **kwargs)
    except httpx.RequestError:
        raise BrevoError("brevo_network", retryable=True) from None


def _http_error(response):
    status = response.status_code
    raise BrevoError(
        f"brevo_http_{status}", retryable=status in (408, 425, 429) or status >= 500,
    )


def _json(response):
    try:
        data = response.json()
    except ValueError:
        raise BrevoError("brevo_invalid_response", retryable=True) from None
    if not isinstance(data, dict):
        raise BrevoError("brevo_invalid_response", retryable=True)
    return data


def _is_duplicate(response):
    if response.status_code != 400:
        return False
    try:
        data = response.json()
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("code") == "duplicate_parameter"


def _get_contact(client, path):
    response = _request(client, "GET", path)
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        _http_error(response)
    contact = _json(response)
    if type(contact.get("id")) is not int or type(contact.get("emailBlacklisted")) is not bool:
        raise BrevoError("brevo_invalid_response", retryable=True)
    for field in ("listIds", "listUnsubscribed"):
        values = contact.get(field, [])
        if not isinstance(values, list) or any(type(value) is not int for value in values):
            raise BrevoError("brevo_invalid_response", retryable=True)
    return contact


def _result(contact, list_id):
    return ContactSyncResult(
        contact_id=contact["id"],
        email_blocked=contact["emailBlacklisted"] or list_id in contact.get("listUnsubscribed", []),
    )


def sync_contact(email: str) -> ContactSyncResult:
    """Create a contact or add its launch-list membership without resubscribing it.

    The caller owns retries. A create conflict is re-read once to accommodate
    another worker creating the same email. At most three bounded requests run.
    Plan selection and consent evidence remain in JobHunt's database: sending
    arbitrary attributes would silently lose data unless provisioned in Brevo.
    """
    api_key = getattr(settings, "JOBHUNT_BREVO_API_KEY", "").strip()
    if not api_key or api_key.startswith("@Microsoft.KeyVault("):
        raise BrevoError("brevo_missing_api_key", retryable=False)
    list_id = getattr(settings, "JOBHUNT_BREVO_LAUNCH_LIST_ID", None)
    if list_id is not None and (type(list_id) is not int or list_id <= 0):
        raise BrevoError("brevo_invalid_list_id", retryable=False)
    try:
        validate_email(email)
    except ValidationError:
        raise BrevoError("brevo_invalid_email", retryable=False) from None

    path = "contacts/" + quote(email, safe="") + "?identifierType=email_id"
    with httpx.Client(
        base_url="https://api.brevo.com/v3/",
        headers={"api-key": api_key, "Accept": "application/json"},
        timeout=10.0,
        follow_redirects=False,
    ) as client:
        contact = _get_contact(client, path)
        if contact is None:
            payload = {"email": email, "updateEnabled": False}
            if list_id is not None:
                payload["listIds"] = [list_id]
            response = _request(client, "POST", "contacts", json=payload)
            if response.status_code != 201:
                # Do not use updateEnabled: a competing writer may have created
                # an unsubscribed contact after our initial lookup.
                if not _is_duplicate(response):
                    _http_error(response)
            contact = _get_contact(client, path)
            if contact is None:
                raise BrevoError("brevo_contact_not_visible", retryable=True)
            result = _result(contact, list_id)
            if not result.email_blocked and list_id is not None and list_id not in contact.get("listIds", []):
                # A racing create may lack our list; let the next attempt add
                # it, keeping this worker attempt to three HTTP requests.
                raise BrevoError("brevo_list_not_visible", retryable=True)
            return result

        result = _result(contact, list_id)
        if result.email_blocked or list_id is None or list_id in contact.get("listIds", []):
            return result

        response = _request(client, "PUT", path, json={"listIds": [list_id]})
        if response.status_code != 204:
            _http_error(response)
        # Reflect a concurrent unsubscribe in the job's final status.
        contact = _get_contact(client, path)
        if contact is None:
            raise BrevoError("brevo_contact_not_visible", retryable=True)
        result = _result(contact, list_id)
        if not result.email_blocked and list_id not in contact.get("listIds", []):
            raise BrevoError("brevo_list_not_visible", retryable=True)
        return result
