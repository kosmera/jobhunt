"""One form per kind of screen. Each exposes ``answers()``: what the machine stores.

The forms read the request the way the browser sends it, with or without
JavaScript: the chips screens accept both hidden inputs (one per chip) and a
comma-separated text field, and merge them (``accounts.forms`` holds those
helpers, shared with the settings page).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import PurePosixPath
from typing import Any

from django import forms
from django.conf import settings

from accounts.forms import chips_text_field, getlist, merge_entries, submitted, text_input
from accounts.models import MAX_CITIES, MAX_JOB_TITLES, Industry, LaunchPlan, SalaryPeriod
from accounts.onboarding.flow import RADIUS_CHOICES, SALARY_BOUNDS, Option
from tracker.models import Language

#: A CV is a PDF or a DOCX; the library accepts more, this screen keeps it simple.
CV_SUFFIXES = frozenset({".pdf", ".docx"})


class LaunchInterestForm(forms.Form):
    """An explicit request for a launch email, independent of paid access."""

    plan = forms.ChoiceField(
        label="La formule qui t'intéresse",
        choices=LaunchPlan.choices,
        widget=forms.RadioSelect,
        error_messages={
            "required": "Choisis la formule qui t'intéresse.",
            "invalid_choice": "Choisis une des deux formules proposées.",
        },
    )
    email = forms.EmailField(
        label="Ton e-mail pour le lancement",
        max_length=254,
        widget=forms.EmailInput(attrs={
            "class": "input", "autocomplete": "email", "inputmode": "email",
            "aria-describedby": "interest-email-help",
        }),
        error_messages={
            "required": "Indique l'e-mail où te prévenir.",
            "invalid": "Indique une adresse e-mail valide.",
        },
    )
    consent = forms.BooleanField(
        label="J'accepte de recevoir une confirmation de mon inscription et un e-mail au lancement de la formule qui m'intéresse.",
        error_messages={"required": "Coche cette case pour recevoir l'e-mail de lancement, ou continue sans t'inscrire."},
    )

    def answers(self) -> dict[str, Any]:
        return {
            "launch_notify": self.cleaned_data["consent"],
            "launch_email": self.cleaned_data["email"],
            "launch_plan": self.cleaned_data["plan"],
        }


class SingleChoiceForm(forms.Form):
    choice = forms.ChoiceField(
        widget=forms.RadioSelect,
        error_messages={"required": "Choisis une réponse.", "invalid_choice": "Choisis une réponse."},
    )

    def __init__(self, *args, key: str, options: Sequence[Option], **kwargs):
        super().__init__(*args, **kwargs)
        self.key = key
        field = self.fields["choice"]
        assert isinstance(field, forms.ChoiceField)
        field.choices = [(option.value, option.label) for option in options]

    def answers(self) -> dict[str, Any]:
        return {self.key: self.cleaned_data["choice"]}


class MultiChoiceForm(forms.Form):
    choice = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple)

    def __init__(
        self,
        *args,
        key: str,
        options: Sequence[Option],
        required_message: str = "Choisis au moins une réponse.",
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.key = key
        self.options = tuple(options)
        field = self.fields["choice"]
        assert isinstance(field, forms.MultipleChoiceField)
        field.choices = [(option.value, option.label) for option in options]
        field.error_messages["required"] = required_message
        field.error_messages["invalid_choice"] = required_message

    def clean_choice(self) -> list[str]:
        """The "all" option stands for every stored value, or alone when it is one itself."""
        selected = list(self.cleaned_data["choice"])
        stored = [option.value for option in self.options if not option.check_all]
        for option in self.options:
            if option.check_all and option.value in selected:
                return [option.value] if option.value in _stored_values(self.options) else stored
        return [value for value in selected if value in stored]

    def answers(self) -> dict[str, Any]:
        return {self.key: self.cleaned_data["choice"]}


def _stored_values(options: Iterable[Option]) -> set[str]:
    """Values the model knows: "Tout ça" is not one, "Peu importe" is."""
    from accounts.models import WorkType

    return {option.value for option in options if option.value in WorkType.values}


class TitlesForm(forms.Form):
    titles_text = chips_text_field(
        "Intitulé ou mot-clé", "Développeur web, comptable, infirmier…",
        source="titles-suggestions", name="titles", limit=MAX_JOB_TITLES, plural="intitulés",
        help_text="Plusieurs postes ? Sépare-les par des virgules.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.titles: list[str] = (
            submitted(self.data, "titles") if self.is_bound else list(self.initial.get("titles") or [])
        )

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        titles = merge_entries(
            getlist(self.data, "titles"), cleaned.get("titles_text", ""),
            limit=MAX_JOB_TITLES, what="Un intitulé", plural="intitulés",
        )
        if not titles:
            raise forms.ValidationError("Indique au moins un poste.")
        self.titles = titles
        return cleaned

    def answers(self) -> dict[str, Any]:
        return {"job_titles": self.titles}


class IndustriesForm(forms.Form):
    any_industry = forms.BooleanField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.industries: list[str] = list(self.initial.get("industries") or [])

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        known = set(Industry.values)
        picked = [value for value in getlist(self.data, "industries") if value in known]
        self.industries = [] if cleaned.get("any_industry") else list(dict.fromkeys(picked))
        return cleaned

    def answers(self) -> dict[str, Any]:
        return {"industries": self.industries, "any_industry": bool(self.cleaned_data.get("any_industry"))}


class CitiesForm(forms.Form):
    cities_text = chips_text_field(
        "Ville ou commune", "Nivelles",
        source="cities-suggestions", name="cities", limit=MAX_CITIES, plural="villes",
        help_text="Plusieurs villes ? Sépare-les par des virgules.",
    )
    search_radius_km = forms.TypedChoiceField(
        label="Rayon autour de la première ville",
        coerce=int,
        choices=[(radius, f"{radius} km") for radius in RADIUS_CHOICES],
        widget=forms.Select(attrs={"class": "input"}),
        error_messages={"required": "Choisis un rayon.", "invalid_choice": "Choisis un rayon."},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cities: list[str] = (
            submitted(self.data, "cities") if self.is_bound else list(self.initial.get("cities") or [])
        )

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        cities = merge_entries(
            getlist(self.data, "cities"), cleaned.get("cities_text", ""),
            limit=MAX_CITIES, what="Une ville", plural="villes",
        )
        if not cities:
            raise forms.ValidationError("Indique au moins une ville, ou passe cette étape.")
        self.cities = cities
        return cleaned

    def answers(self) -> dict[str, Any]:
        return {"cities": self.cities, "search_radius_km": self.cleaned_data["search_radius_km"]}


class SalaryForm(forms.Form):
    salary_period = forms.ChoiceField(
        choices=SalaryPeriod.choices,
        widget=forms.RadioSelect,
        initial=SalaryPeriod.MONTH,
        error_messages={"required": "Choisis une période.", "invalid_choice": "Choisis une période."},
    )
    salary_min = forms.IntegerField(
        label="Minimum",
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "input", "inputmode": "numeric", "min": 1}),
        error_messages={
            "required": "Un montant, s'il te plaît.",
            "invalid": "Un montant, s'il te plaît.",
            "min_value": "Un montant, s'il te plaît.",
        },
    )

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        period = cleaned.get("salary_period")
        amount = cleaned.get("salary_min")
        if period in SALARY_BOUNDS and amount is not None and amount > SALARY_BOUNDS[period][0]:
            self.add_error("salary_min", "Ce montant semble hors limites pour cette période.")
        return cleaned

    def answers(self) -> dict[str, Any]:
        return {"salary_min": self.cleaned_data["salary_min"], "salary_period": self.cleaned_data["salary_period"]}


class IdentityForm(forms.Form):
    display_name = forms.CharField(
        label="Comment t'appeler ?",
        max_length=120,
        widget=text_input("Lionel", autofocus=True, autocomplete="name"),
        error_messages={"required": "Il faut bien un nom."},
    )

    def clean_display_name(self) -> str:
        value = self.cleaned_data["display_name"].strip()
        if not value:
            raise forms.ValidationError("Il faut bien un nom.")
        return value

    def answers(self) -> dict[str, Any]:
        return {"display_name": self.cleaned_data["display_name"]}


def _megabytes(size: int) -> int:
    return -(-size // (1024 * 1024))


class CVForm(forms.Form):
    file = forms.FileField(
        label="Ton CV",
        # Visually hidden: the dropzone label around it opens the picker, and the
        # focus ring lands on the dropzone (``.dropzone:has(:focus-visible)``).
        widget=forms.ClearableFileInput(attrs={"class": "visually-hidden"}),
        error_messages={"required": "Choisis un fichier."},
    )
    language = forms.ChoiceField(
        label="Langue du CV",
        choices=Language.choices,
        widget=forms.Select(attrs={"class": "input"}),
        error_messages={"required": "Choisis la langue du CV.", "invalid_choice": "Choisis la langue du CV."},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        suffixes = sorted(CV_SUFFIXES, reverse=True)
        self.fields["file"].widget.attrs["accept"] = ",".join(suffixes)
        formats = " ou ".join(f"{suffix[1:].upper()} ({suffix})" for suffix in suffixes)
        limit = _megabytes(settings.FILE_UPLOAD_MAX_MEMORY_SIZE)
        self.fields["file"].help_text = f"{formats} · {limit} Mo max."

    def clean_file(self):
        upload = self.cleaned_data["file"]
        suffix = PurePosixPath(upload.name or "").suffix.lower()
        if suffix not in CV_SUFFIXES:
            raise forms.ValidationError("Format non pris en charge : PDF ou DOCX.")
        limit = settings.FILE_UPLOAD_MAX_MEMORY_SIZE
        if upload.size > limit:
            raise forms.ValidationError(f"Fichier trop lourd : {_megabytes(limit)} Mo max.")
        return upload
