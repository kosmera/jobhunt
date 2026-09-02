"""Who is using the tracker, and how they like it to behave.

The stock ``auth.User`` stays the account: swapping ``AUTH_USER_MODEL`` after
``auth``/``admin`` migrations have run breaks every existing database. What
the user model does not carry lives in two one-to-one rows:

- ``Profile`` — identity shown in the interface (name, home base). Its
  ``onboarded_at`` doubles as the "has been through onboarding" flag.
- ``Preferences`` — the knobs that used to be environment variables (follow-up
  delay, staleness threshold) plus the search radius and default CV language.

Both are created with the user (signal) and, defensively, on first access
(``services.profile_for`` / ``services.preferences_for``).
"""

from __future__ import annotations

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


# The environment variables keep their meaning as *defaults for a new
# profile*; module-level callables so migrations serialise a reference, not
# whatever value the environment held when ``makemigrations`` ran.
def default_stale_after_days() -> int:
    return settings.STALE_AFTER_DAYS


def default_follow_up_days() -> int:
    return settings.DEFAULT_FOLLOW_UP_DAYS


def default_search_radius_km() -> int:
    return settings.DEFAULT_SEARCH_RADIUS_KM


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    display_name = models.CharField("nom affiché", max_length=120, blank=True)
    headline = models.CharField(
        "titre professionnel",
        max_length=200,
        blank=True,
        help_text="Ce que tu cherches, en une ligne : « Ingénieur DevOps senior ».",
    )
    location = models.CharField(
        "point de départ",
        max_length=200,
        blank=True,
        help_text="La ville d'où se comptent les distances des offres.",
    )
    onboarded_at = models.DateTimeField("profil complété le", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "profil"
        verbose_name_plural = "profils"

    def __str__(self) -> str:
        return self.display_name or self.user.get_username()

    @property
    def is_onboarded(self) -> bool:
        return self.onboarded_at is not None

    @property
    def initials(self) -> str:
        source = self.display_name.strip() or self.user.get_username()
        parts = [part for part in source.replace("-", " ").split() if part]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()


class Preferences(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="preferences"
    )
    stale_after_days = models.PositiveSmallIntegerField(
        "sans nouvelles après (jours)",
        default=default_stale_after_days,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text="Au-delà, une candidature envoyée remonte dans « À traiter maintenant ».",
    )
    follow_up_days = models.PositiveSmallIntegerField(
        "relance proposée à (jours)",
        default=default_follow_up_days,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text="Délai entre l'envoi d'une candidature et la relance programmée.",
    )
    search_radius_km = models.PositiveSmallIntegerField(
        "rayon de recherche (km)",
        default=default_search_radius_km,
        validators=[MinValueValidator(5), MaxValueValidator(300)],
        help_text="Autour de ton point de départ.",
    )
    default_cv_language = models.CharField(
        "langue de CV par défaut",
        max_length=2,
        choices=[("fr", "Français"), ("en", "Anglais"), ("nl", "Néerlandais")],
        default="fr",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "préférences"
        verbose_name_plural = "préférences"

    def __str__(self) -> str:
        return f"Préférences de {self.user.get_username()}"
