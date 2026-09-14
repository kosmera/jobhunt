"""Authentication policy shared by the application and Django administration."""

from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import User
from typing import cast

from accounts import conf
from accounts.models import Profile
from rls import as_user


class AccountsBackend(ModelBackend):
    """Keep local/development passwords; production sessions require a verified email."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if conf.passwordless():
            return None
        return super().authenticate(request, username=username, password=password, **kwargs)

    def get_user(self, user_id):
        user = cast(User | None, super().get_user(user_id))
        if user is None or not conf.passwordless():
            return user
        email = user.email.strip().casefold()
        if not email:
            return None
        # Session restoration runs before the request has an RLS identity.
        # Read the verification on this account's behalf without creating it.
        with as_user(user):
            verified = Profile.objects.filter(
                user=user, email_verified_at__isnull=False,
            ).values_list("verified_email", flat=True).first()
        if not verified or verified.strip().casefold() != email:
            return None
        return user
