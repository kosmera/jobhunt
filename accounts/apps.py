from importlib import import_module

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Comptes"

    def ready(self):
        from rls import register

        # One row per account on both tables: the policy compares ``user_id``.
        register("accounts.Profile", owner="user")
        register("accounts.Preferences", owner="user")
        # Registers the post_save receiver and the configuration checks.
        import_module("accounts.signals")
        import_module("accounts.checks")
