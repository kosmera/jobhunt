from importlib import import_module

from django.apps import AppConfig
from django.conf import settings


class RlsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "rls"
    verbose_name = "Isolation des données"

    def ready(self):
        from rls import registry

        # Tables Django ships and the application does not own. Every account
        # sees its own ``auth_user`` row once signed in; a session with no
        # account bound (login, signup, session lookup) sees them all —
        # authentication needs the row before it knows who is asking.
        registry.register(settings.AUTH_USER_MODEL, owner="id", unbound_visible=True)
        registry.register("auth.User_groups", owner="user")
        registry.register("auth.User_user_permissions", owner="user")
        registry.register("admin.LogEntry", owner="user")

        import_module("rls.checks")
        import_module("rls.signals")
