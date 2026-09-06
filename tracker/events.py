"""Core workflow events. Importing this module does not load models or AI."""

from django.dispatch import Signal

application_owner_changed = Signal()
cv_ingested = Signal()


class CVEventPublisher:
    """Publish anonymized CV intake to any installed subscriber."""

    def analyze_cv(self, owner, *, document_id, label, language, text) -> None:
        cv_ingested.send(
            sender=type(self),
            owner=owner,
            document_id=document_id,
            label=label,
            language=language,
            text=text,
        )
