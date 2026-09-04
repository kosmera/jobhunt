"""The ORM adapter: every port realised with Django models.

Stateless — the database is the state — and engine-agnostic: SQLite and
PostgreSQL both go through it. Two rules keep it honest across engines:

- every ordering on a nullable key is written with ``nulls_last=True``
  (SQLite sorts NULL as the smallest value, PostgreSQL as the largest);
  ``Meta.ordering`` is never relied on;
- writes go through ``instance.save()`` / ``instance.delete()`` so model
  hooks and signals fire (slug generation, owner propagation, the
  ``post_delete`` receiver that removes a document's file). The only
  ``QuerySet.update`` is the bulk demotion of primary documents.

``atomic()`` is Django's transaction. Note what a rollback cannot undo, as
before this layer existed: a document deleted then rolled back has already
lost its file (the signal ran), and an upload rolled back leaves the file on
disk (``FileField.pre_save`` writes it before the INSERT).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from django.db import transaction
from django.db.models import Count, F

from tracker.models import ActivityEvent, Application, Company, Contact, Document, SkillGap
from tracker.ports import NotFound


class DjangoApplications:
    def _base(self, owner):
        return Application.objects.for_user(owner).with_related()

    def get(self, owner, pk: int) -> Application:
        try:
            return Application.objects.with_related().get(owner_id=owner.pk, pk=pk)
        except Application.DoesNotExist:
            raise NotFound(f"Candidature {pk} introuvable pour ce compte.") from None

    def detail(self, owner, pk: int) -> Application:
        try:
            return (
                Application.objects.with_related()
                .prefetch_related("documents", "contacts", "events")
                .get(owner_id=owner.pk, pk=pk)
            )
        except Application.DoesNotExist:
            raise NotFound(f"Candidature {pk} introuvable pour ce compte.") from None

    def add(self, application: Application) -> Application:
        application.save()
        return application

    def save(self, application: Application, *, fields: Iterable[str] | None = None) -> None:
        if fields is None:
            application.save()
        else:
            # ``auto_now`` only fires for fields listed in ``update_fields``.
            application.save(update_fields=[*fields, "updated_at"])

    def remove(self, application: Application) -> None:
        application.delete()

    def by_status(self, owner, statuses: Iterable[str]) -> list[Application]:
        return list(
            self._base(owner)
            .filter(status__in=list(statuses))
            .order_by(F("score").desc(nulls_last=True), "company__name", "pk")
        )

    def status_counts(self, owner) -> dict[str, int]:
        rows = (
            Application.objects.for_user(owner)
            .order_by()
            .values("status")
            .annotate(n=Count("id"))
        )
        return {row["status"]: row["n"] for row in rows}

    def needing_attention(self, owner, *, stale_days: int, on: dt.date) -> list[Application]:
        return list(
            self._base(owner)
            .needs_attention(stale_days, on)
            .order_by(
                F("follow_up_on").asc(nulls_last=True),
                F("applied_on").asc(nulls_last=True),
                "pk",
            )
        )

    def attention_count(self, owner, *, stale_days: int, on: dt.date) -> int:
        return Application.objects.for_user(owner).needs_attention(stale_days, on).count()

    def scores(self, owner, statuses: Iterable[str]) -> list[int]:
        return list(
            Application.objects.for_user(owner)
            .active()
            .filter(status__in=list(statuses), score__isnull=False)
            .order_by()
            .values_list("score", flat=True)
        )


class DjangoCompanies:
    def get_or_create(self, owner, name: str, sector: str | None = None) -> Company:
        name = name.strip()
        # The uniqueness constraint is on the exact name, so "Acme" and
        # "ACME" can coexist (admin, import): the first one wins rather than
        # a case-insensitive ``get`` raising on the pair.
        company = Company.objects.filter(owner=owner, name__iexact=name).order_by("pk").first()
        if company is None:
            extra = {"sector": sector} if sector else {}
            return Company.objects.create(owner=owner, name=name, **extra)
        if sector and company.sector != sector:
            company.sector = sector
            company.save(update_fields=["sector"])
        return company


class DjangoEvents:
    def get(self, owner, pk: int) -> ActivityEvent:
        try:
            return ActivityEvent.objects.select_related("application").get(
                pk=pk, application__owner_id=owner.pk
            )
        except ActivityEvent.DoesNotExist:
            raise NotFound(f"Événement {pk} introuvable pour ce compte.") from None

    def add(
        self,
        application: Application,
        kind: str,
        title: str,
        detail: str = "",
        on: dt.date | None = None,
    ) -> ActivityEvent:
        # ``Application.log`` is the public model API; the port is its
        # persistence-agnostic face.
        return application.log(kind, title, detail, on)

    def remove(self, event: ActivityEvent) -> None:
        event.delete()


class DjangoDocuments:
    def get(self, owner, pk: int) -> Document:
        try:
            return Document.objects.select_related("application").get(owner_id=owner.pk, pk=pk)
        except Document.DoesNotExist:
            raise NotFound(f"Document {pk} introuvable pour ce compte.") from None

    def add(self, document: Document) -> Document:
        document.save()
        return document

    def remove(self, document: Document) -> None:
        document.delete()  # the post_delete signal removes the file from disk

    def demote_primary(self, application: Application, kind: str) -> None:
        Document.objects.filter(application=application, kind=kind).update(is_primary=False)

    def count(self, owner) -> int:
        return Document.objects.for_user(owner).count()

    def owns_file(self, owner, file_name: str) -> bool:
        if not file_name:
            return False
        return Document.objects.for_user(owner).filter(file=file_name).exists()


class DjangoContacts:
    def get(self, owner, pk: int) -> Contact:
        try:
            return Contact.objects.select_related("application").get(
                pk=pk, application__owner_id=owner.pk
            )
        except Contact.DoesNotExist:
            raise NotFound(f"Contact {pk} introuvable pour ce compte.") from None

    def add(self, contact: Contact) -> Contact:
        contact.save()
        return contact

    def remove(self, contact: Contact) -> None:
        contact.delete()


class DjangoSkillGaps:
    def get(self, owner, pk: int) -> SkillGap:
        try:
            return SkillGap.objects.get(owner_id=owner.pk, pk=pk)
        except SkillGap.DoesNotExist:
            raise NotFound(f"Lacune {pk} introuvable pour ce compte.") from None

    def save(self, gap: SkillGap, *, fields: Iterable[str] | None = None) -> None:
        if fields is None:
            gap.save()
        else:
            gap.save(update_fields=list(fields))


class DjangoPersistence:
    """The production adapter, whatever the engine behind the ORM."""

    def __init__(self):
        self.applications = DjangoApplications()
        self.companies = DjangoCompanies()
        self.events = DjangoEvents()
        self.documents = DjangoDocuments()
        self.contacts = DjangoContacts()
        self.skill_gaps = DjangoSkillGaps()

    def atomic(self):
        return transaction.atomic()

    def on_commit(self, callback):
        # Outside a transaction Django runs the callback on the spot.
        transaction.on_commit(callback)
