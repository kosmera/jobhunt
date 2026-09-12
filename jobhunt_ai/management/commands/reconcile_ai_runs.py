from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from jobhunt_ai.services.runner import sweep_orphans


class Command(BaseCommand):
    help = "Clôt les tâches IA expirées, même sans navigateur ni worker actif."

    def add_arguments(self, parser):
        parser.add_argument("--owner-id", type=int)

    def handle(self, *args, **options):
        users = get_user_model().objects.order_by("pk").values_list("pk", flat=True)
        if options["owner_id"] is not None:
            users = users.filter(pk=options["owner_id"])
        # auth_user is unbound-visible by design; each protected table is
        # accessed separately in as_user. No BYPASSRLS worker is required.
        count = sum(sweep_orphans(owner_id) for owner_id in users.iterator(chunk_size=500))
        self.stdout.write(f"{count} exécution(s) expirée(s).")
