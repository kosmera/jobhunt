"""Receivers of the core workflow events, connected only by AppConfig.ready().

``tracker.events.cv_ingested`` carries owner, document_id, label, language,
text (an anonymized object with text/redactions), and make_primary (optional).
``tracker.events.application_owner_changed`` carries application and
previous_owner_id.
"""

from jobhunt_ai.access import has_copilot_access

_connections = []


def receive_cv(
    sender, owner, document_id, label, language, text, make_primary=False, **kwargs
):
    """Enqueue durably; the ORM message becomes visible only after commit."""
    from tracker.models import Document, DocumentKind

    from jobhunt_ai.models import RunKind
    from jobhunt_ai.services import runner

    # Free accounts still store their CV normally, without scheduling paid work.
    if not has_copilot_access(owner):
        return None
    if not Document.objects.filter(
        owner=owner, pk=document_id, kind=DocumentKind.CV
    ).exists():
        raise ValueError("The CV document must belong to the supplied account.")
    return runner.launch(
        RunKind.PARSE_CV,
        owner=owner,
        params={
            "document_id": document_id,
            "label": label,
            "language": language,
            "make_primary": make_primary,
            "text": text.text,
            "redactions": dict(text.redactions),
        },
    )


def follow_application_owner(sender, application, previous_owner_id, **kwargs):
    """Transfer copied ownership; results continue to follow their relation."""
    from tracker.models import Application

    from jobhunt_ai.models import AgentRun, OfferLead

    if not isinstance(application, Application):
        return
    owner_id = application.owner_id
    alias = application._state.db or "default"
    for model in (AgentRun, OfferLead):
        model.objects.using(alias).filter(application=application).exclude(
            owner_id=owner_id
        ).update(owner_id=owner_id)


def register_receivers():
    """Reentrant registration: repeated ``ready()`` calls never duplicate."""
    from tracker import events

    for signal, uid in _connections:
        signal.disconnect(dispatch_uid=uid)
    _connections.clear()
    bindings = [
        (events.cv_ingested, receive_cv, "jobhunt_ai.cv_ingested"),
        (
            events.application_owner_changed,
            follow_application_owner,
            "jobhunt_ai.follow_application_owner",
        ),
    ]
    for signal, receiver, uid in bindings:
        signal.connect(receiver, dispatch_uid=uid, weak=False)
        _connections.append((signal, uid))
