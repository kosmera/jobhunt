"""Mode flags for the templates. Profile and preferences travel on the request
(``request.profile`` / ``request.preferences``, set by the middleware) so they
cannot shadow a view's own ``profile`` variable."""

from __future__ import annotations

from django.contrib.auth import get_user_model

from accounts import conf


def account(request):
    context = {"auth_is_local": conf.is_local(), "signup_open": conf.signup_open()}
    user = getattr(request, "user", None)
    if conf.is_local() and user is not None and user.is_authenticated:
        # Whether the rail offers "switch profile": pointless with one profile.
        context["local_profile_count"] = get_user_model().objects.filter(is_active=True).count()
    return context
