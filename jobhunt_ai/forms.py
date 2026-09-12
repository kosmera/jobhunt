"""Formulaires du copilote."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from django import forms

from jobhunt_ai import conf
from jobhunt_ai.services.accounts import search_preferences
from jobhunt_ai.services.documents import (
    UnsupportedFormat,
    check_extension,
    cv_documents,
)

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile

    from tracker.models import Document


class CVUploadForm(forms.Form):
    """Analyser un CV : un nouveau fichier, ou un document déjà téléversé."""

    file = forms.FileField(
        label="Nouveau fichier",
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "input"}),
        help_text="PDF, DOCX, TXT ou MD.",
    )
    document = forms.ModelChoiceField(
        label="Ou un CV déjà dans les documents",
        queryset=None,
        required=False,
        widget=forms.Select(attrs={"class": "input"}),
    )
    label = forms.CharField(
        label="Libellé du profil",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "input",
                "placeholder": "Laisser vide pour reprendre le nom du fichier",
            }
        ),
    )
    make_primary = forms.BooleanField(
        label="En faire le profil principal", required=False, initial=True
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        field = cast("forms.ModelChoiceField[Document]", self.fields["document"])
        field.queryset = cv_documents(user.pk)
        field.empty_label = "— choisir un document —"

    def clean(self):
        cleaned = super().clean() or self.cleaned_data
        upload: UploadedFile | None = cleaned.get("file")
        document: Document | None = cleaned.get("document")
        if upload and document:
            raise forms.ValidationError(
                "Un seul à la fois : fichier ou document existant."
            )
        if upload:
            filename = upload.name or ""
        elif document:
            filename = document.file.name or ""
        else:
            raise forms.ValidationError("Choisis un fichier ou un document existant.")
        try:
            check_extension(filename)
        except UnsupportedFormat as exc:
            raise forms.ValidationError(str(exc))
        return cleaned


class ScoutForm(forms.Form):
    """Lancer une veille d'offres, autour du point de départ du profil."""

    keywords = forms.CharField(
        label="Mots-clés",
        max_length=500,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "input",
                "placeholder": "Laisser vide pour dériver du profil",
            }
        ),
    )
    location = forms.CharField(
        label="Localisation",
        max_length=200,
        initial=conf.DEFAULT_LOCATION,
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    radius_km = forms.IntegerField(
        label="Rayon (km)",
        initial=conf.DEFAULT_RADIUS_KM,
        min_value=5,
        max_value=300,
        widget=forms.NumberInput(attrs={"class": "input"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            location, radius = search_preferences(user)
            self.fields["location"].initial = location or conf.DEFAULT_LOCATION
            self.fields["radius_km"].initial = radius
