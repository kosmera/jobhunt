"""Account operations shared by the views, the middleware and the tests."""

from __future__ import annotations

from django.contrib import auth
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import Preferences, Profile

#: Username of the password-less account that the ownership migration creates
#: to hold data recorded before accounts existed.
LOCAL_USERNAME = "local"

#: The backend recorded on the session when a user is signed in without a
#: password (local mode). ``auth.login`` needs one; ``ModelBackend`` is the
#: one that will later answer ``has_perm``/``get_user`` for that session.
LOCAL_BACKEND = "django.contrib.auth.backends.ModelBackend"


def owned_or_404(queryset, user, **kwargs):
    """``get_object_or_404`` restricted to what ``user`` owns.

    Accepts a model or a queryset; the second form keeps ``select_related``
    and friends. Another account's row is indistinguishable from a missing one.
    """
    if not hasattr(queryset, "filter"):
        queryset = queryset._default_manager.all()
    return get_object_or_404(queryset, owner=user, **kwargs)


def profile_for(user) -> Profile:
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def preferences_for(user) -> Preferences:
    preferences, _ = Preferences.objects.get_or_create(user=user)
    return preferences


def unique_username(base: str) -> str:
    """A username derived from a display name, suffixed if already taken."""
    User = get_user_model()
    stem = slugify(base)[:140] or "profil"
    candidate, counter = stem, 2
    while User.objects.filter(username__iexact=candidate).exists():
        candidate = f"{stem}-{counter}"
        counter += 1
    return candidate


@transaction.atomic
def create_local_user(display_name: str):
    """A password-less account for local mode."""
    User = get_user_model()
    user = User(username=unique_username(display_name))
    user.set_unusable_password()
    user.save()
    return user


def complete_onboarding(user, *, display_name: str, headline: str = "", location: str = "") -> Profile:
    profile = profile_for(user)
    profile.display_name = display_name.strip()
    profile.headline = headline.strip()
    profile.location = location.strip()
    profile.onboarded_at = profile.onboarded_at or timezone.now()
    profile.save()
    preferences_for(user)
    return profile


def sign_in_without_password(request, user) -> None:
    """Local mode only: the caller has already checked ``conf.is_local()``."""
    auth.login(request, user, backend=LOCAL_BACKEND)


def delete_account(request, user) -> None:
    """Remove the account and every row it owns.

    ``Application.company`` is ``PROTECT``: deleting the user directly would
    cascade into companies while applications still point at them and Django
    would refuse. Applications go first (taking events, contacts, documents
    and plugin rows with them), then the user (companies, platforms, gaps,
    library documents, profile, preferences). Signing out first keeps the
    local-mode middleware from re-authenticating the doomed account mid-way.
    """
    from tracker.models import Application

    auth.logout(request)
    with transaction.atomic():
        Application.objects.filter(owner=user).delete()
        user.delete()
