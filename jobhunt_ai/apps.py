"""Validate the configuration and register receivers after model loading."""

from importlib import import_module

from django.apps import AppConfig


class JobHuntAIConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "jobhunt_ai"
    verbose_name = "Copilote IA"

    def ready(self):
        """Idempotent and free of database I/O: Django may call it more than once."""
        import rls

        from jobhunt_ai.validation import validate_settings

        validate_settings()
        import_module("jobhunt_ai.checks")
        # Racines : profils, exécutions, pistes et quotas portent leur compte.
        for label in ("CandidateProfile", "AgentRun", "OfferLead", "UserAIQuota"):
            rls.register(f"jobhunt_ai.{label}", owner="owner")
        # Résultats attachés à une candidature : visibles quand elle l'est.
        for label in ("MatchReport", "GeneratedCV"):
            rls.register(f"jobhunt_ai.{label}", via="application")
        rls.register("jobhunt_ai.ScoutTarget", via="run")
        # La file Django-Q2 n'appartient à personne : elle ne porte que des
        # identifiants d'exécution, jamais de texte de CV.
        for label in ("OrmQ", "Task", "Schedule"):
            rls.exempt(f"django_q.{label}")
        signals = import_module("jobhunt_ai.signals")
        signals.register_receivers()
