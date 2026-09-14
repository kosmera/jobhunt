"""Recover email work within each tenant's normal application role."""

import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from accounts.email_delivery import reconcile_user
from accounts.magic_links import reconcile_sign_in_links


class Command(BaseCommand):
    help = "Reprend les traitements e-mail en attente ; --backfill inclut les accords déjà collectés."

    def add_arguments(self, parser):
        parser.add_argument("--owner-id", type=int)
        parser.add_argument("--backfill", action="store_true", help="Crée les traitements manquants des inscrits consentants.")
        parser.add_argument("--retry-failed", action="store_true", help="Relance les échecs après correction de leur cause.")
        parser.add_argument("--watch", action="store_true", help="Vérifie toutes les 60 secondes jusqu'à interruption.")

    def handle(self, *args, **options):
        try:
            while True:
                # auth_user is intentionally visible without a tenant. Every
                # protected row is accessed within reconcile_user's as_user.
                users = get_user_model().objects.order_by("pk").values_list("pk", flat=True)
                if options["owner_id"] is not None:
                    users = users.filter(pk=options["owner_id"])
                count = sum(reconcile_user(
                    user_id, backfill=options["backfill"], retry_failed=options["retry_failed"],
                ) for user_id in users.iterator(chunk_size=500))
                count += reconcile_sign_in_links(user_id=options["owner_id"])
                self.stdout.write(f"{count} traitement(s) e-mail mis en file.")
                if not options["watch"]:
                    break
                # Explicit failure resets are a one-time operator action, never
                # a loop that defeats the automatic attempt limit.
                options["retry_failed"] = False
                close_old_connections()
                time.sleep(60)
        except KeyboardInterrupt:
            self.stdout.write("Surveillance e-mail arrêtée.")
