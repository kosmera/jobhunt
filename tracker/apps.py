from importlib import import_module

from django.apps import AppConfig


class TrackerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tracker"
    verbose_name = "Suivi de candidatures"

    def ready(self):
        # Registers the configuration checks.
        import_module("tracker.checks")
