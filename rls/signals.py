"""A sign-in during a request rebinds the rest of that request.

Local mode signs the visitor in from ``AccountsMiddleware.process_view``, the
profile chooser and the signup view call ``auth.login``: from that moment the
request acts for the new account. Signing out does not unbind — deleting an
account signs out first, then deletes the rows, which must stay visible.
"""

from __future__ import annotations

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from rls import context


@receiver(user_logged_in, dispatch_uid="rls.rebind_on_login")
def rebind_on_login(sender, request, user, **kwargs):
    context.rebind(user)
