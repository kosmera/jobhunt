"""``manage.py storage_prune`` — the files no document points at any more.

A document's bytes are written before its row, so that the row never names
a file that is not there. The cost is the other direction: if the request
that was going to write the row fails after the write, the bytes stay.
Django can undo a transaction, not a blob — and no rollback hook would help
against a process killed between the two anyway.

So the imbalance is swept rather than prevented. Everything under
``documents/`` that no ``Document`` row names, and that has been sitting
there longer than ``--older-than`` hours, is an orphan. The delay matters:
an upload in flight has its bytes stored and its row not yet written, and
it must not be collected out from under itself.

Reads every account's documents, so it runs with the credentials that can
see them all — the owner role on PostgreSQL, like ``dumpdata`` and the
backups (see « Isolation des données » in the README). Prints what it would
delete; ``--delete`` actually removes.
"""

from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tracker.adapters import storage
from tracker.models import Document
from tracker.ports import FILES_PREFIX, MissingFile, StorageError

#: How long a file may be unreferenced before it counts as abandoned.
DEFAULT_GRACE_HOURS = 24


class Command(BaseCommand):
    help = "Liste (et supprime avec --delete) les fichiers qu'aucun document ne référence."

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete", action="store_true", help="Supprime réellement ; sans lui, rien n'est touché."
        )
        parser.add_argument(
            "--older-than",
            type=int,
            default=DEFAULT_GRACE_HOURS,
            metavar="HEURES",
            help=(
                "N'examine que les fichiers déposés il y a plus de N heures "
                f"(défaut : {DEFAULT_GRACE_HOURS}). Un téléversement en cours a ses octets "
                "écrits et sa ligne pas encore : ne le ramasse pas."
            ),
        )

    def handle(self, *args, **options):
        grace = options["older_than"]
        if grace < 0:
            raise CommandError("--older-than attend un nombre d'heures positif.")
        files = storage()
        cutoff = timezone.now() - dt.timedelta(hours=grace)

        known = set(Document.objects.exclude(file="").values_list("file", flat=True))
        self.stdout.write(f"{len(known)} fichier(s) référencé(s) par un document.")

        orphans, skipped, freed = [], 0, 0
        try:
            for name in files.list_files(FILES_PREFIX.rstrip("/")):
                if name in known:
                    continue
                try:
                    if files.file_modified_at(name) > cutoff:
                        skipped += 1
                        continue
                    freed += files.file_size(name)
                except (MissingFile, NotImplementedError, OSError):
                    # Gone under our feet, or a backend that does not date
                    # its files: report it, let the operator decide.
                    pass
                orphans.append(name)
        except StorageError as exc:
            raise CommandError(f"Le stockage ne répond pas : {exc}") from exc

        for name in sorted(orphans):
            self.stdout.write(f"  orphelin  {name}")
        if skipped:
            self.stdout.write(f"{skipped} fichier(s) récent(s) ignoré(s) (moins de {grace} h).")
        if not orphans:
            self.stdout.write(self.style.SUCCESS("Aucun fichier orphelin."))
            return

        size = f" ({freed / 1024:.1f} Ko)" if freed else ""
        if not options["delete"]:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(orphans)} fichier(s) orphelin(s){size} — relance avec --delete pour les supprimer."
                )
            )
            return
        for name in orphans:
            files.delete_file(name)
        self.stdout.write(self.style.SUCCESS(f"{len(orphans)} fichier(s) supprimé(s){size}."))
