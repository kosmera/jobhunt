"""Durable launch mail and contact sync. Queue messages contain only job/user IDs.

The ORM message and job commit with the consent. Network calls happen outside
tenant transactions. An ambiguous SMTP result is never retried automatically.
"""

from __future__ import annotations

import logging
import smtplib
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import close_old_connections, connection, transaction
from django.db.models import F
from django.template.loader import render_to_string
from django.utils import timezone
from django_q.conf import Conf
from django_q.exceptions import TimeoutException
from django_q.tasks import async_task

from accounts.brevo import BrevoError, sync_contact
from accounts.models import EmailJobKind, EmailJobStatus, LaunchEmailJob, LaunchPlan, Profile
from rls import as_user

logger = logging.getLogger(__name__)


class DeliveryError(Exception):
    def __init__(self, code: str, status=EmailJobStatus.FAILED):
        super().__init__(code)
        self.code = code
        self.status = status


def _queue(job: LaunchEmailJob) -> None:
    if Conf.ORM != "default" or Conf.SYNC:
        raise ImproperlyConfigured("Email jobs require Q2 orm='default', sync=False.")
    job.task_id = async_task(
        "accounts.email_delivery.execute_queued", job.pk, job.user_id,
        q_options={"sync": False, "ack_failure": True, "timeout": settings.JOBHUNT_EMAIL_JOB_TIMEOUT},
    )
    job.queued_at = timezone.now()
    job.save(update_fields=["task_id", "queued_at"])


def enqueue_launch_emails(profile: Profile) -> int:
    """Two effects per account, once; includes already collected opt-ins on backfill."""
    with as_user(profile.user_id), transaction.atomic():
        profile = Profile.objects.select_for_update().select_related("user").get(pk=profile.pk)
        if not (profile.user.is_active and profile.is_onboarded and profile.launch_consent_at
                and profile.launch_email and profile.launch_plan in LaunchPlan.values):
            return 0
        created_count = 0
        for kind in EmailJobKind.values:
            job, created = LaunchEmailJob.objects.get_or_create(user_id=profile.user_id, kind=kind, defaults={
                "email": profile.launch_email.strip().lower(), "plan": profile.launch_plan,
                "consent_at": profile.launch_consent_at,
            })
            if created:
                _queue(job)
                created_count += 1
        return created_count


def _eligible(job: LaunchEmailJob) -> bool:
    return Profile.objects.filter(
        user_id=job.user_id, user__is_active=True, onboarded_at__isnull=False,
        launch_email__iexact=job.email, launch_plan=job.plan, launch_consent_at=job.consent_at,
    ).exists()


def _smtp_rejection(exc) -> DeliveryError:
    code = getattr(exc, "smtp_code", 500)
    return DeliveryError("smtp_rejected", EmailJobStatus.RETRY if 400 <= code < 500 else EmailJobStatus.FAILED)


def send_welcome(job: LaunchEmailJob) -> None:
    """An acknowledged SMTP send succeeds even if the subsequent QUIT fails."""
    if not settings.EMAIL_HOST:
        raise DeliveryError("smtp_not_configured")
    try:
        context = {"plan": dict(LaunchPlan.choices)[job.plan], "public_url": settings.JOBHUNT_PUBLIC_URL}
        mailer = get_connection()
        message = EmailMultiAlternatives(
            subject="Bienvenue sur tonjobidéal !",
            body=render_to_string("accounts/emails/welcome.txt", context),
            from_email=settings.DEFAULT_FROM_EMAIL, to=[job.email], connection=mailer,
            headers={"Message-ID": f"<launch-welcome-{job.pk}@tonjobideal.com>"},
        )
        message.attach_alternative(render_to_string("accounts/emails/welcome.html", context), "text/html")
        # Serialize before connecting so invalid headers/configuration cannot
        # be mistaken for an ambiguous delivery during the SMTP operation.
        message.message()
    except Exception:
        raise DeliveryError("smtp_configuration_error") from None
    try:
        try:
            mailer.open()
        except smtplib.SMTPResponseException as exc:
            raise _smtp_rejection(exc) from None
        except smtplib.SMTPServerDisconnected:
            raise DeliveryError("smtp_connection_failed", EmailJobStatus.RETRY) from None
        except smtplib.SMTPException:
            raise DeliveryError("smtp_configuration_error") from None
        except OSError:
            raise DeliveryError("smtp_connection_failed", EmailJobStatus.RETRY) from None
        try:
            sent = message.send(fail_silently=False)
        except smtplib.SMTPRecipientsRefused as exc:
            temporary = all(400 <= response[0] < 500 for response in exc.recipients.values())
            raise DeliveryError("smtp_recipient_rejected", EmailJobStatus.RETRY if temporary else EmailJobStatus.FAILED) from None
        except smtplib.SMTPResponseException as exc:
            raise _smtp_rejection(exc) from None
        except OSError:
            raise DeliveryError("smtp_delivery_unknown", EmailJobStatus.UNCERTAIN) from None
        if sent != 1:
            raise DeliveryError("smtp_no_message_sent")
    finally:
        try:
            mailer.close()
        except Exception:
            # SMTP accepted DATA already, or the original failure is more useful.
            pass


def _finish(job: LaunchEmailJob, status: str, code: str = "") -> None:
    now = timezone.now()
    if status == EmailJobStatus.RETRY and job.attempts >= settings.JOBHUNT_EMAIL_MAX_ATTEMPTS:
        status = EmailJobStatus.FAILED
    delay = min(60 * 5 ** max(0, job.attempts - 1), 3600)
    with as_user(job.user_id):
        LaunchEmailJob.objects.filter(
            pk=job.pk, user_id=job.user_id, status=EmailJobStatus.RUNNING, claim_token=job.claim_token,
        ).update(
            status=status, error_code=code, lease_until=None,
            next_attempt_at=now + timedelta(seconds=delay),
            finished_at=None if status == EmailJobStatus.RETRY else now,
        )


def execute_queued(job_id: int, user_id: int) -> None:
    """Claim conditionally; another message cannot re-execute a running/completed effect."""
    owns_connection = not connection.in_atomic_block
    if owns_connection:
        close_old_connections()
    try:
        with as_user(user_id), transaction.atomic():
            now = timezone.now()
            token = uuid.uuid4()
            claimed = LaunchEmailJob.objects.filter(
                pk=job_id, user_id=user_id, status=EmailJobStatus.PENDING, next_attempt_at__lte=now,
            ).update(
                status=EmailJobStatus.RUNNING, claim_token=token, started_at=now,
                attempts=F("attempts") + 1, error_code="",
                lease_until=now + timedelta(seconds=settings.JOBHUNT_EMAIL_JOB_TIMEOUT + 60),
            )
            if not claimed:
                return
            job = LaunchEmailJob.objects.get(pk=job_id, user_id=user_id, claim_token=token)
            eligible = _eligible(job)
        if not eligible:
            _finish(job, EmailJobStatus.CANCELLED, "consent_or_account_changed")
            return
        try:
            if job.kind == EmailJobKind.WELCOME:
                send_welcome(job)
            elif job.kind == EmailJobKind.CONTACT_SYNC:
                result = sync_contact(job.email)
                if result.email_blocked:
                    _finish(job, EmailJobStatus.SUPPRESSED, "brevo_unsubscribed")
                    return
            else:
                raise DeliveryError("unknown_job_kind")
        except BrevoError as exc:
            _finish(job, EmailJobStatus.RETRY if exc.retryable else EmailJobStatus.FAILED, exc.code)
        except DeliveryError as exc:
            _finish(job, exc.status, exc.code)
        except TimeoutException:
            _finish(job, EmailJobStatus.UNCERTAIN if job.kind == EmailJobKind.WELCOME else EmailJobStatus.RETRY, "worker_timeout")
            raise
        except Exception:
            _finish(job, EmailJobStatus.UNCERTAIN if job.kind == EmailJobKind.WELCOME else EmailJobStatus.FAILED, "unexpected_error")
        else:
            _finish(job, EmailJobStatus.SUCCEEDED)
    except Exception:
        # Queue diagnostics are shared: never copy a provider/SQL error or an address.
        logger.error("Email job %s could not complete; inspect its delivery status.", job_id)
        raise RuntimeError(f"Email job {job_id} could not complete.") from None
    finally:
        if owns_connection:
            close_old_connections()


def reconcile_user(user_id: int, *, backfill=False, retry_failed=False) -> int:
    """Recover due retries/lost work. Interrupted SMTP sends need manual resolution."""
    queued = 0
    with as_user(user_id), transaction.atomic():
        profile = Profile.objects.filter(user_id=user_id).first()
        if backfill and profile:
            queued += enqueue_launch_emails(profile)
        now = timezone.now()
        jobs = LaunchEmailJob.objects.select_for_update().filter(user_id=user_id).exclude(status__in=[
            EmailJobStatus.SUCCEEDED, EmailJobStatus.CANCELLED, EmailJobStatus.SUPPRESSED, EmailJobStatus.UNCERTAIN,
        ])
        for job in jobs:
            if job.status == EmailJobStatus.RUNNING and job.lease_until and job.lease_until <= now:
                if job.kind == EmailJobKind.WELCOME:
                    _finish(job, EmailJobStatus.UNCERTAIN, "worker_interrupted")
                    continue
                _finish(job, EmailJobStatus.RETRY, "worker_interrupted")
                continue
            retry = job.status == EmailJobStatus.RETRY and job.next_attempt_at <= now
            lost = job.status == EmailJobStatus.PENDING and (
                job.queued_at is None or job.queued_at <= now - timedelta(seconds=Conf.RETRY)
            )
            manual = retry_failed and job.status == EmailJobStatus.FAILED
            if not (retry or lost or manual):
                continue
            if not _eligible(job):
                job.status = EmailJobStatus.CANCELLED
                job.finished_at = now
                job.error_code = "consent_or_account_changed"
                job.save(update_fields=["status", "finished_at", "error_code"])
                continue
            job.status = EmailJobStatus.PENDING
            job.next_attempt_at = now
            job.finished_at = None
            job.error_code = ""
            if manual:
                job.attempts = 0
            job.save(update_fields=["status", "next_attempt_at", "finished_at", "error_code", "attempts"])
            _queue(job)
            queued += 1
    return queued
