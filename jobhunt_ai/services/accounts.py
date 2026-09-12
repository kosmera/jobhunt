"""Ce que le copilote lit du compte : identité et préférences de recherche."""

from dataclasses import dataclass

from accounts.services import preferences_for, profile_for


@dataclass(frozen=True)
class Identity:
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""


def identity(user) -> Identity:
    """L'identité vient du compte : le CV remis au modèle est anonymisé."""
    profile = profile_for(user)
    return Identity(
        full_name=profile.display_name or user.get_username(),
        email=user.email,
        phone=profile.phone,
        location=profile.location,
    )


def search_preferences(user) -> tuple[str, int]:
    """Point de départ d'une veille : localisation du profil et rayon préféré."""
    return profile_for(user).location, preferences_for(user).search_radius_km
