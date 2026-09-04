"""``manage.py storage_status`` — what the file provider says about itself.

The configuration check (``tracker.checks.check_storage``) stays offline;
this command is the one that talks to the provider. It names the backend and
the credentials, makes sure the container is really there — a mistyped name
answers "no such file" to every probe otherwise, and only fails at the first
upload — and mints a throwaway link, which is what exercises the signing
material. ``--probe`` goes one step further and writes then deletes a small
blob, the way ``rls_status --probe`` checks the database role.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tracker.adapters import storage
from tracker.ports import FILES_PREFIX, StorageError

#: A name no document can take: the account id is never zero.
PROBE = f"{FILES_PREFIX}0/sonde/jobhunt-storage-status.txt"


class Command(BaseCommand):
    help = "Décrit le stockage des fichiers configuré et vérifie qu'il répond."

    def add_arguments(self, parser):
        parser.add_argument(
            "--probe",
            action="store_true",
            help="Écrit puis supprime un fichier d'essai (vérifie les droits d'écriture).",
        )

    def handle(self, *args, **options):
        files = storage()
        backend = files.__class__
        self.stdout.write(f"Fournisseur : {settings.STORAGE_PROVIDER}")
        self.stdout.write(f"Adaptateur  : {backend.__module__}.{backend.__qualname__}")
        location = getattr(files, "location", None)
        if location:
            self.stdout.write(f"Emplacement : {location}")
        for label, value in getattr(files, "describe", dict)().items():
            self.stdout.write(f"{label:<12}: {value}")
        self.stdout.write(
            "Diffusion   : "
            + ("par l'application (vue signée)" if files.served_by_app else "par le fournisseur (lien SAS)")
        )

        try:
            check_container = getattr(files, "check_container", None)
            if check_container is not None:
                check_container()
                self.stdout.write("Conteneur   : présent")
            present = files.file_exists(PROBE)
            self.stdout.write(f"Sonde       : {'présente (inattendu)' if present else 'absente, comme prévu'}")
            link = files.get_secure_url(PROBE)
            self.stdout.write(f"Lien        : {link[:80]}{'…' if len(link) > 80 else ''}")
            if options["probe"]:
                name = files.save_file(PROBE, b"jobhunt")
                try:
                    written = files.open_file(name).read()
                finally:
                    files.delete_file(name)
                self.stdout.write(f"Écriture    : {len(written)} octets écrits, relus et supprimés")
        except StorageError as exc:
            raise CommandError(f"Le stockage ne répond pas : {exc}") from exc

        self.stdout.write(self.style.SUCCESS("Stockage : ok"))
