"""Every account gets its ``Profile`` and ``Preferences`` rows on creation.

``services.profile_for`` / ``preferences_for`` remain the primary way to reach
them: they create the rows on demand for users that bypassed the signal
(fixtures, raw SQL, historical models in migrations).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from accounts import conf
from accounts.models import Preferences, Profile
from accounts.services import ensure_local_admin
from rls import as_user


@receiver(pre_save, sender=get_user_model(), dispatch_uid="accounts.passwordless_password")
def discard_production_password(sender, instance, update_fields=None, using="default", **kwargs):
    """Admin and management-command saves must not introduce password hashes."""
    if not conf.passwordless() or not instance.has_usable_password():
        return
    instance.set_unusable_password()
    if instance.pk and update_fields is not None and "password" not in update_fields:
        # A partial save (including Django's last_login update) would otherwise
        # leave an old password in the database despite the in-memory change.
        sender._default_manager.using(using).filter(pk=instance.pk).update(password=instance.password)


@receiver(user_logged_in, dispatch_uid="accounts.local_admin")
def provision_local_admin(sender, user, **kwargs):
    """Local sign-in establishes installation ownership before serving pages."""
    ensure_local_admin(user)


@receiver(post_save, sender=get_user_model(), dispatch_uid="accounts.create_profile_rows")
def create_profile_rows(sender, instance, created, raw=False, **kwargs):
    if raw or not created:
        return
    # A user is created before anyone is signed in as it (signup, onboarding,
    # createsuperuser): the rows are written on the new account's behalf, or
    # the database would refuse them.
    with as_user(instance):
        Profile.objects.get_or_create(user=instance)
        Preferences.objects.get_or_create(user=instance)
