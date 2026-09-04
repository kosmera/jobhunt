from importlib import import_module

from django.apps import AppConfig


class TrackerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tracker"
    verbose_name = "Suivi de candidatures"

    def ready(self):
        from rls import register

        # Root rows by their owner; events and contacts through their application.
        for label in ("Company", "Platform", "SkillGap", "Application", "Document"):
            register(f"tracker.{label}", owner="owner")
        register("tracker.ActivityEvent", via="application")
        register("tracker.Contact", via="application")
        # Registers the configuration checks.
        import_module("tracker.checks")
