"""Every account gets its ``Profile`` and ``Preferences`` rows on creation.

``services.profile_for`` / ``preferences_for`` remain the primary way to reach
them: they create the rows on demand for users that bypassed the signal
(fixtures, raw SQL, historical models in migrations).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import Preferences, Profile
from rls import as_user


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
