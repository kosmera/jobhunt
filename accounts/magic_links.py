"""Email identity proofs, delivered by the existing durable Django-Q worker.

Only a random record ID is queued/stored. Its bearer signature is generated
in the worker with a dedicated signing salt and never saved in the database.
"""

from datetime import timedelta
import uuid

from django.conf import settings
from django.contrib.auth.models import User
from django.core import signing
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.mail.backends.console import EmailBackend as ConsoleEmailBackend
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django_q.tasks import async_task

from accounts import conf
from accounts.models import EmailLinkRateLimit, EmailSignInLink
from accounts.onboarding.flow import machine
from accounts.onboarding.machine import Run
from accounts.services import finish_onboarding, name_profile, profile_for
from rls import as_user

LIFETIME = timedelta(minutes=15)
SALT = "accounts.email-sign-in.v1"
MAX_SEND_ATTEMPTS = 3
SEND_LEASE = timedelta(seconds=60)


def sign_link(link):
    return signing.TimestampSigner(salt=SALT).sign(str(link.pk))


def _queue(link_id):
    return async_task("accounts.magic_links.send_link", str(link_id), q_options={"timeout": 60})


def _holders(email):
    return list(User.objects.filter(
        Q(email__iexact=email) | Q(username__iexact=email),
    )[:2])


def _allow_request(email, ip):
    """Atomic counters across web workers, including unknown/inactive addresses."""
    now = timezone.now()
    for kind, value, seconds, limit in (
        ("ip-hour", ip, 3600, 20),
        ("email-minute", email, 60, 1),
        ("email-hour", email, 3600, 5),
    ):
        bucket = int(now.timestamp()) // seconds
        digest = salted_hmac("accounts.email-rate", f"{kind}:{value}").hexdigest()
        key = f"{digest}:{bucket}"
        EmailLinkRateLimit.objects.get_or_create(
            key=key, defaults={"expires_at": now + timedelta(seconds=seconds)},
        )
        changed = EmailLinkRateLimit.objects.filter(key=key, count__lt=limit).update(count=F("count") + 1)
        if not changed:
            return False
    return True


def request_link(email, *, ip="", purpose="login", user=None, display_name="", onboarding_data=None, next_path=""):
    """Always give callers the same outcome, regardless of account existence."""
    if not conf.passwordless():
        return
    email = email.strip().lower()
    with as_user(None), transaction.atomic():
        if not _allow_request(email, ip):
            return
        holders = _holders(email)
        if purpose == "change":
            if user is None or not user.is_active or holders:
                return
        elif holders:
            if len(holders) != 1 or not holders[0].is_active or holders[0].email.lower() != email:
                return
            user = holders[0]
            purpose = "login"
            # A signup request must never overwrite an existing user's name
            # or questionnaire with answers supplied by somebody else.
            onboarding_data = None
            display_name = ""
        elif purpose != "signup" or not conf.signup_open():
            return
        link = EmailSignInLink.objects.create(
            email=email, user=user, purpose=purpose,
            original_email=user.email if user is not None else "",
            display_name=display_name, onboarding_data=onboarding_data or {},
            next_path=next_path, expires_at=timezone.now() + LIFETIME,
        )
        # The ORM broker write participates in the same transaction; rollback
        # leaves neither a request nor a task. No bearer token enters the queue.
        _queue(link.pk)


def send_link(link_id):
    """Claim delivery once; the reconciler recovers failed or interrupted attempts."""
    if not conf.passwordless():
        return
    now = timezone.now()
    with as_user(None), transaction.atomic():
        candidates = EmailSignInLink.objects.filter(
            pk=link_id, consumed_at__isnull=True, sent_at__isnull=True,
            expires_at__gt=now, next_attempt_at__lte=now, attempts__lt=MAX_SEND_ATTEMPTS,
        ).filter(Q(sending_at__isnull=True) | Q(sending_at__lte=now - SEND_LEASE))
        if not candidates.update(sending_at=now, attempts=F("attempts") + 1):
            return
        link = EmailSignInLink.objects.get(pk=link_id)
        if not _eligible(link):
            EmailSignInLink.objects.filter(pk=link.pk).update(sending_at=None, next_attempt_at=link.expires_at)
            return
    try:
        connection = get_connection()
        # File-based mail inherits the console backend. Neither delivers a
        # message, and both would persist a live sign-in proof outside email.
        if isinstance(connection, ConsoleEmailBackend):
            raise RuntimeError("auth_email_backend_not_deliverable")
        context = {
            "link_url": settings.JOBHUNT_PUBLIC_URL + reverse("accounts:email_link_confirm", args=[sign_link(link)]),
            "signup": link.purpose == "signup", "email_change": link.purpose == "change",
            "expires_minutes": 15,
        }
        subject = {
            "signup": "Confirme ton compte tonjobidéal",
            "login": "Ton lien de connexion tonjobidéal",
            "change": "Confirme ton nouvel e-mail tonjobidéal",
        }[link.purpose]
        message = EmailMultiAlternatives(
            subject, render_to_string("accounts/emails/auth_link.txt", context),
            settings.DEFAULT_FROM_EMAIL, [link.email], connection=connection,
            headers={"Message-ID": f"<auth-{link.pk}@tonjobideal.com>"},
        )
        message.attach_alternative(render_to_string("accounts/emails/auth_link.html", context), "text/html")
        if message.send() != 1:
            raise RuntimeError("email_not_accepted")
    except Exception:
        with as_user(None):
            EmailSignInLink.objects.filter(pk=link.pk, sending_at=now).update(
                sending_at=None, next_attempt_at=timezone.now() + timedelta(seconds=60 * link.attempts),
            )
        # Queue failures must not log provider responses, recipients or links.
        raise RuntimeError("auth_email_delivery_failed") from None
    with as_user(None):
        EmailSignInLink.objects.filter(pk=link.pk, sending_at=now).update(sent_at=timezone.now(), sending_at=None)


def reconcile_sign_in_links(user_id=None):
    """Queue valid due work; the send claim makes duplicate queued UUIDs harmless."""
    if not conf.passwordless():
        return 0
    now = timezone.now()
    with as_user(None), transaction.atomic():
        links = EmailSignInLink.objects.filter(
            consumed_at__isnull=True, sent_at__isnull=True, expires_at__gt=now,
            next_attempt_at__lte=now, attempts__lt=MAX_SEND_ATTEMPTS,
        ).filter(Q(sending_at__isnull=True) | Q(sending_at__lte=now - SEND_LEASE))
        if user_id is not None:
            links = links.filter(user_id=user_id)
        queued = 0
        for link in links.iterator(chunk_size=100):
            if _eligible(link):
                _queue(link.pk)
                queued += 1
        return queued


def _eligible(link):
    holders = _holders(link.email)
    if link.purpose == "signup":
        return conf.signup_open() and not holders and link.user_id is None
    user = User.objects.filter(pk=link.user_id, is_active=True).first()
    if user is None or user.email != link.original_email:
        return False
    if link.purpose == "change":
        return not holders
    return len(holders) == 1 and holders[0].pk == user.pk and user.email.lower() == link.email


def read_link(token):
    if not conf.passwordless():
        return None
    try:
        link_id = uuid.UUID(signing.TimestampSigner(salt=SALT).unsign(token, max_age=LIFETIME))
    except (signing.BadSignature, ValueError):
        return None
    with as_user(None):
        link = EmailSignInLink.objects.filter(
            pk=link_id, consumed_at__isnull=True, expires_at__gt=timezone.now(),
        ).first()
        return link if link is not None and _eligible(link) else None


def consume_link(token):
    """Claim once, verify identity, then return the user for login OUTSIDE as_user."""
    link = read_link(token)
    if link is None:
        return None
    try:
        with as_user(None), transaction.atomic():
            # Conditional UPDATE serializes even two simultaneous POSTs on SQLite.
            if not EmailSignInLink.objects.filter(
                pk=link.pk, consumed_at__isnull=True, expires_at__gt=timezone.now(),
            ).update(consumed_at=timezone.now()):
                return None
            if not _eligible(link):
                return None
            if link.purpose == "signup":
                user = User.objects.create_user(username=link.email, email=link.email, password=None)
            else:
                user = User.objects.select_for_update().get(pk=link.user_id)
                # Recheck after the account lock: another link may have changed it.
                if not user.is_active or user.email != link.original_email:
                    return None
            with as_user(user):
                if link.purpose == "signup":
                    name_profile(user, link.display_name)
                if link.purpose == "change":
                    # Reserve the canonical address through auth.User's unique
                    # username too, including accounts imported with a username.
                    user.username = link.email
                user.email = link.email
                # Also removes historical hashes and rotates the session auth hash
                # when changing an address, expiring all previous sessions.
                if user.has_usable_password() or link.purpose == "change":
                    user.set_unusable_password()
                user.save(update_fields=["password", "email", "username"])
                profile = profile_for(user)
                profile.verified_email = link.email
                profile.email_verified_at = timezone.now()
                profile.save(update_fields=["verified_email", "email_verified_at"])
                if link.purpose == "signup":
                    run = Run.from_json(link.onboarding_data)
                    if run is not None and run.state == machine.terminal:
                        # This server-built snapshot is released only by email
                        # proof. Persist answers and account creation together,
                        # including when the link opens on another device.
                        finish_onboarding(user, run.answers)
            EmailSignInLink.objects.filter(pk=link.pk).update(user=user, onboarding_data={})
            return user, link
    except IntegrityError:
        # Another confirmation reserved this username/address concurrently.
        return None
