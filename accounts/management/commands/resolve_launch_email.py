"""Resolve one ambiguous SMTP outcome after inspecting Brevo delivery logs."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.email_delivery import reconcile_user
from accounts.models import EmailJobStatus, LaunchEmailJob
from rls import as_user


class Command(BaseCommand):
    help = "Résout un envoi incertain après vérification des journaux Brevo (aucune relance automatique)."

    def add_arguments(self, parser):
        parser.add_argument("--owner-id", type=int, required=True)
        parser.add_argument("--job-id", type=int, required=True)
        outcome = parser.add_mutually_exclusive_group(required=True)
        outcome.add_argument("--delivered", action="store_true", help="Brevo confirme que le message a été accepté.")
        outcome.add_argument("--not-delivered", action="store_true", help="Brevo confirme l'absence d'envoi ; autorise un nouvel essai.")

    def handle(self, *args, **options):
        with as_user(options["owner_id"]), transaction.atomic():
            job = LaunchEmailJob.objects.select_for_update().filter(
                pk=options["job_id"], user_id=options["owner_id"], status=EmailJobStatus.UNCERTAIN,
            ).first()
            if job is None:
                raise CommandError("Aucun envoi incertain correspondant à ce compte et cet identifiant.")
            if options["delivered"]:
                job.status = EmailJobStatus.SUCCEEDED
                job.finished_at = timezone.now()
                job.error_code = "manually_confirmed"
            else:
                job.status = EmailJobStatus.RETRY
                job.attempts = 0
                job.next_attempt_at = timezone.now()
                job.finished_at = None
                job.error_code = ""
            job.save(update_fields=["status", "attempts", "next_attempt_at", "finished_at", "error_code"])
            if options["not_delivered"]:
                reconcile_user(job.user_id)
        self.stdout.write(f"Traitement e-mail {options['job_id']} résolu.")
