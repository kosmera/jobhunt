from importlib import import_module

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Comptes"

    def ready(self):
        # Registers the post_save receiver and the configuration checks.
        import_module("accounts.signals")
        import_module("accounts.checks")
