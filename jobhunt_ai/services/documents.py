"""Prise en charge des CV et rangement des documents générés.

La lecture, l'anonymisation et le stockage des fichiers passent par le port
de stockage du cœur : seul du texte anonymisé sort d'ici vers les agents.
"""

from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import PurePosixPath

from django.core.files.base import ContentFile

import rls
from accounts.services import profile_for
from tracker import privacy, services
from tracker.adapters import document_text, storage
from tracker.adapters.document_text import UnsupportedFormat
from tracker.models import Document, DocumentKind

__all__ = [
    "ALLOWED_EXTENSIONS",
    "CVSubmission",
    "MIN_TEXT_LENGTH",
    "UnsupportedFormat",
    "check_extension",
    "generated_document",
    "prepare_cv",
]

ALLOWED_EXTENSIONS = document_text.SUPPORTED_SUFFIXES
MIN_TEXT_LENGTH = services.MIN_TEXT_LENGTH


@dataclass(frozen=True)
class CVSubmission:
    """Only anonymized text and label may cross this boundary."""

    document_id: int
    label: str
    language: str
    text: str
    redactions: dict[str, int]


def check_extension(filename):
    """Return an accepted suffix or a user-facing validation error."""
    extension = PurePosixPath(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(e.lstrip(".").upper() for e in ALLOWED_EXTENSIONS))
        raise UnsupportedFormat(
            f"Format non pris en charge ({extension or 'sans extension'}). "
            f"Formats acceptés : {allowed}."
        )
    return extension


def cv_documents(owner_id):
    """Les CV du compte, le principal en tête."""
    return Document.objects.filter(owner_id=owner_id, kind=DocumentKind.CV).order_by(
        "-is_primary", "label"
    )


class _CVCollector:
    """Capture already anonymized intake without invoking or knowing an agent."""

    submission = None

    def analyze_cv(self, owner, *, document_id, label, language, text):
        self.submission = CVSubmission(
            document_id,
            label,
            language,
            text.text,
            dict(text.redactions),
        )


def prepare_cv(user, *, document=None, upload=None, label="") -> CVSubmission | None:
    """Store/read/anonymize a CV. Return CVSubmission or None if unreadable.

    Verify document ownership, anonymize the label too, and translate reader
    failures to UnsupportedFormat or OSError. Do not enqueue AI.
    """
    profile = profile_for(user)
    known = privacy.known_identity(
        display_name=profile.display_name,
        username=user.get_username(),
        email=user.email,
        location=profile.location,
    )
    if document is None:
        if upload is None:
            raise ValueError("Provide a CV document or an uploaded file.")
        collector = _CVCollector()
        services.ingest_cv(
            user,
            None,
            Document(kind=DocumentKind.CV),
            upload=upload,
            label=label or upload.name,
            known=known,
            analyzer=collector,
        )
        return collector.submission
    document = cv_documents(user.pk).get(pk=document.pk)
    text = storage().extract_and_anonymize_text(document.file.name or "", known=known)
    if len(text.text.strip()) < MIN_TEXT_LENGTH:
        return None
    return CVSubmission(
        document.pk,
        privacy.anonymize(label or document.label, known=known).text,
        document.language,
        text.text,
        dict(text.redactions),
    )


@contextmanager
def generated_document(application, payload, language):
    """Context manager yielding a saved document for rendered bytes.

    Upload outside the DB transaction, then yield inside an account-bound
    transaction. Delete uploaded bytes if the caller's DB writes fail.
    """
    document = Document(
        owner=application.owner,
        application=application,
        kind=DocumentKind.CV,
        label=f"CV IA — {application.company.name} ({language.upper()})",
        language=language,
    )
    document.file.save(
        f"cv-ia-{application.slug}-{language}.docx",
        ContentFile(payload),
        save=False,
    )
    document.size_bytes = len(payload)
    try:
        with rls.as_user(application.owner_id):
            document.save()
            yield document
    except BaseException:
        with suppress(OSError):
            storage().delete_file(document.file.name or "")
        raise
