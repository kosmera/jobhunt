"""Exercise the real launch-email jobs using one reusable local test account."""

import hashlib
import os

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import connection, transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django_q.models import OrmQ
from django_q.signing import SignedPackage

from accounts.email_delivery import enqueue_launch_emails, execute_queued, reconcile_user
from accounts.models import EmailJobStatus, LaunchEmailJob, LaunchPlan, Profile, SubscriptionLevel
from jobhunt.database import is_local_host
from rls import as_user


TEST_MARKER = "JobHunt email workflow test"
TEST_DISPLAY_NAME = "Test e-mails (local)"
TERMINAL_STATUSES = {
    EmailJobStatus.SUCCEEDED, EmailJobStatus.FAILED, EmailJobStatus.UNCERTAIN,
    EmailJobStatus.SUPPRESSED, EmailJobStatus.CANCELLED,
}


def _username(email):
    return "jobhunt-email-test-" + hashlib.sha256(email.encode()).hexdigest()[:32]


def _require_local_environment():
    local_database = connection.vendor == "sqlite" or (
        connection.vendor == "postgresql" and is_local_host(connection.settings_dict.get("HOST"))
    )
    if (not settings.DEBUG or settings.IS_SAAS_PRODUCTION or not local_database
            or os.environ.get("WEBSITE_INSTANCE_ID") or os.environ.get("WEBSITE_SITE_NAME")):
        raise CommandError("Ce test est réservé au développement local, avec DEBUG actif et une base locale.")


def _prepare_account(email, plan, retry_failed):
    with transaction.atomic():
        user, created = User.objects.get_or_create(
            username=_username(email), defaults={
                "email": email, "first_name": TEST_MARKER, "password": make_password(None),
            },
        )
        if (user.email != email or user.first_name != TEST_MARKER or user.has_usable_password()
                or user.is_staff or user.is_superuser or not user.is_active):
            raise CommandError("Le compte réservé au test existe déjà avec d'autres données ; aucune modification effectuée.")
        with as_user(user.pk):
            profile = Profile.objects.get(user=user)
            if created:
                now = timezone.now()
                profile.display_name = TEST_DISPLAY_NAME
                profile.onboarded_at = now
                profile.launch_email = email
                profile.launch_plan = plan
                profile.launch_consent_at = now
                profile.subscription_level = SubscriptionLevel.FREE
                profile.save()
            elif (profile.display_name != TEST_DISPLAY_NAME or profile.launch_email != email
                  or profile.launch_plan != plan or not profile.launch_consent_at or not profile.is_onboarded):
                raise CommandError("Ce compte de test a déjà d'autres données ou une autre formule ; aucune modification effectuée.")
            enqueue_launch_emails(profile)
            reconcile_user(user.pk, retry_failed=retry_failed)
        return user.pk


def _remove_completed_messages(user_id, invocation_jobs):
    """Delete only the exact signed messages whose effects completed here."""
    with as_user(user_id):
        completed = {
            (job.pk, job.task_id) for job in LaunchEmailJob.objects.filter(
                user_id=user_id, pk__in=invocation_jobs, status__in=TERMINAL_STATUSES,
            ) if invocation_jobs[job.pk] == job.task_id
        }
    if not completed:
        return
    for row in OrmQ.objects.iterator(chunk_size=100):
        try:
            task = SignedPackage.loads(row.payload)
        except Exception:
            continue
        if (isinstance(task, dict) and task.get("func") == "accounts.email_delivery.execute_queued"
                and isinstance(task.get("args"), tuple) and len(task["args"]) == 2
                and type(task["args"][0]) is int and type(task["args"][1]) is int
                and task["args"][1] == user_id and (task["args"][0], task.get("id")) in completed):
            OrmQ.objects.filter(pk=row.pk).delete()


class Command(BaseCommand):
    help = "Prévisualise la bienvenue ; --send teste les deux traitements sur un compte local dédié, sans parcours ni qcluster."

    def add_arguments(self, parser):
        parser.add_argument("--to", required=True, help="Adresse de destination du test.")
        parser.add_argument("--plan", choices=LaunchPlan.values, default=LaunchPlan.PREMIUM)
        parser.add_argument("--send", action="store_true", help="Envoie réellement la bienvenue et synchronise le contact Brevo.")
        parser.add_argument("--retry-failed", action="store_true", help="Avec --send, reprend les échecs après correction des réglages.")

    def handle(self, *args, **options):
        _require_local_environment()
        if options["retry_failed"] and not options["send"]:
            raise CommandError("--retry-failed nécessite --send.")
        email = options["to"].strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            raise CommandError("Une adresse e-mail valide est nécessaire.") from None
        if len(email) > 254:
            raise CommandError("Une adresse e-mail valide est nécessaire.")
        plan = options["plan"]
        if not options["send"]:
            context = {"plan": dict(LaunchPlan.choices)[plan], "public_url": settings.JOBHUNT_PUBLIC_URL}
            body = render_to_string("accounts/emails/welcome.txt", context)
            render_to_string("accounts/emails/welcome.html", context)
            self.stdout.write(f"Aperçu pour {email} — aucun compte créé, aucun envoi ni contact Brevo modifié.\n")
            self.stdout.write(body)
            self.stdout.write("Les versions texte et HTML sont prêtes. Ajouter --send pour exécuter le test réel.")
            return

        user_id = _prepare_account(email, plan, options["retry_failed"])
        with as_user(user_id):
            invocation_jobs = dict(LaunchEmailJob.objects.filter(
                user_id=user_id, status=EmailJobStatus.PENDING, next_attempt_at__lte=timezone.now(),
            ).values_list("pk", "task_id"))
        # The normal worker runs outside tenant transactions, on these IDs only.
        for job_id in invocation_jobs:
            execute_queued(job_id, user_id)
        _remove_completed_messages(user_id, invocation_jobs)
        with as_user(user_id):
            jobs = list(LaunchEmailJob.objects.filter(user_id=user_id).order_by("pk"))
        self.stdout.write(f"Compte de test local : {user_id}. Un envoi réussi n'est jamais répété automatiquement.")
        for job in jobs:
            detail = f" ({job.error_code})" if job.error_code else ""
            self.stdout.write(f"{job.kind} #{job.pk} : {job.status}{detail}")
        if any(job.status not in {EmailJobStatus.SUCCEEDED, EmailJobStatus.SUPPRESSED} for job in jobs):
            raise CommandError("Le test n'est pas entièrement terminé. Consulter les états ci-dessus ; --retry-failed reprend uniquement les échecs corrigés.")
