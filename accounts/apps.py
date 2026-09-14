from importlib import import_module

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Comptes"

    def ready(self):
        from rls import register

        # One row per account on each table: the policy compares ``user_id``.
        register("accounts.Profile", owner="user")
        register("accounts.Preferences", owner="user")
        register("accounts.SearchProfile", owner="user")
        register("accounts.LaunchEmailJob", owner="user")
        # Like auth.User, identity requests must be accessible before login.
        # A signed UUID is required by the redemption view; IDs alone are inert.
        register("accounts.EmailSignInLink", owner="user", unbound_visible=True)
        register("accounts.EmailLinkRateLimit", owner="user", unbound_visible=True)
        # The shared queue carries job/account IDs, never recipients or CVs.
        from rls import exempt
        for label in ("OrmQ", "Task", "Schedule"):
            exempt(f"django_q.{label}")
        # Registers the post_save receiver and the configuration checks.
        import_module("accounts.signals")
        import_module("accounts.checks")
