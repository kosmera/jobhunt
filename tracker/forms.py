"""Forms for creating and editing tracked applications and their satellites."""

from __future__ import annotations

from django import forms
from django.utils import timezone

from tracker.models import (
    Application,
    Company,
    Contact,
    Document,
    DocumentKind,
    ActivityEvent,
    EventKind,
    Language,
    Sector,
    Status,
    WorkMode,
)

DATE_ATTRS = {"type": "date", "class": "input"}


class DateField(forms.DateField):
    """A date field that renders as a native picker and accepts ISO input."""

    widget = forms.DateInput(attrs=DATE_ATTRS, format="%Y-%m-%d")

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)


class ApplicationForm(forms.ModelForm):
    """Full editor for an application, with an inline 'new company' escape hatch."""

    company_name = forms.CharField(
        label="Société",
        max_length=200,
        help_text="Tape un nom existant ou un nouveau : la société est créée au besoin.",
        widget=forms.TextInput(attrs={"class": "input", "list": "company-options",
                                      "autocomplete": "off"}),
    )
    company_sector = forms.ChoiceField(
        label="Secteur",
        choices=Sector.choices,
        initial=Sector.PRIVATE,
        widget=forms.Select(attrs={"class": "input"}),
    )

    class Meta:
        model = Application
        fields = [
            "title",
            "location",
            "distance_km",
            "work_mode",
            "score",
            "source_platform",
            "url",
            "cv_language",
            "status",
            "discovered_on",
            "applied_on",
            "follow_up_on",
            "summary",
            "strengths",
            "weaknesses",
            "strategy",
            "personal_notes",
            "discard_reason",
            "posting_raw",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "input", "placeholder": "Senior DevOps Engineer"}),
            "location": forms.TextInput(attrs={"class": "input", "placeholder": "Bruxelles"}),
            "distance_km": forms.NumberInput(attrs={"class": "input", "min": 0, "max": 999}),
            "work_mode": forms.Select(attrs={"class": "input"}),
            "score": forms.NumberInput(attrs={"class": "input", "min": 0, "max": 100}),
            "source_platform": forms.Select(attrs={"class": "input"}),
            "url": forms.URLInput(attrs={"class": "input", "placeholder": "https://…"}),
            "cv_language": forms.Select(attrs={"class": "input"}),
            "status": forms.Select(attrs={"class": "input"}),
            "discovered_on": forms.DateInput(attrs=DATE_ATTRS, format="%Y-%m-%d"),
            "applied_on": forms.DateInput(attrs=DATE_ATTRS, format="%Y-%m-%d"),
            "follow_up_on": forms.DateInput(attrs=DATE_ATTRS, format="%Y-%m-%d"),
            "summary": forms.Textarea(attrs={"class": "input", "rows": 3}),
            "strengths": forms.Textarea(attrs={"class": "input", "rows": 6}),
            "weaknesses": forms.Textarea(attrs={"class": "input", "rows": 6}),
            "strategy": forms.Textarea(attrs={"class": "input", "rows": 5}),
            "personal_notes": forms.Textarea(attrs={"class": "input", "rows": 5}),
            "discard_reason": forms.Textarea(attrs={"class": "input", "rows": 3}),
            "posting_raw": forms.Textarea(attrs={"class": "input mono", "rows": 10}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_platform"].empty_label = "— aucune —"
        self.fields["source_platform"].required = False
        for name in ("discovered_on", "applied_on", "follow_up_on"):
            self.fields[name].input_formats = ["%Y-%m-%d", "%d/%m/%Y"]
        if self.instance.pk and self.instance.company_id:
            self.fields["company_name"].initial = self.instance.company.name
            self.fields["company_sector"].initial = self.instance.company.sector

    def clean_score(self):
        score = self.cleaned_data.get("score")
        if score is not None and not 0 <= score <= 100:
            raise forms.ValidationError("La compatibilité s'exprime entre 0 et 100.")
        return score

    def clean(self):
        cleaned = super().clean()
        applied = cleaned.get("applied_on")
        follow_up = cleaned.get("follow_up_on")
        if applied and follow_up and follow_up < applied:
            self.add_error(
                "follow_up_on", "La relance ne peut pas précéder l'envoi de la candidature."
            )
        if cleaned.get("status") == Status.DISCARDED and not cleaned.get("discard_reason"):
            self.add_error(
                "discard_reason", "Note pourquoi tu écartes l'offre : ça évite de refaire le tri."
            )
        return cleaned

    def save(self, commit=True):
        application = super().save(commit=False)
        name = self.cleaned_data["company_name"].strip()
        sector = self.cleaned_data["company_sector"]
        company, created = Company.objects.get_or_create(
            name__iexact=name, defaults={"name": name, "sector": sector}
        )
        if not created and company.sector != sector:
            company.sector = sector
            company.save(update_fields=["sector"])
        application.company = company
        if commit:
            application.save()
        return application


class QuickApplicationForm(forms.ModelForm):
    """The 'I just spotted an offer' form: the five fields that matter."""

    company_name = forms.CharField(
        label="Société",
        max_length=200,
        widget=forms.TextInput(attrs={"class": "input", "list": "company-options",
                                      "autocomplete": "off", "autofocus": "autofocus"}),
    )

    class Meta:
        model = Application
        fields = ["title", "url", "score", "cv_language", "status"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "input"}),
            "url": forms.URLInput(attrs={"class": "input", "placeholder": "https://…"}),
            "score": forms.NumberInput(attrs={"class": "input", "min": 0, "max": 100}),
            "cv_language": forms.Select(attrs={"class": "input"}),
            "status": forms.Select(attrs={"class": "input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].initial = Status.BACKLOG
        self.fields["url"].required = False

    def save(self, commit=True):
        application = super().save(commit=False)
        name = self.cleaned_data["company_name"].strip()
        company, _ = Company.objects.get_or_create(
            name__iexact=name, defaults={"name": name}
        )
        application.company = company
        application.discovered_on = timezone.localdate()
        if commit:
            application.save()
        return application


class StatusChangeForm(forms.Form):
    status = forms.ChoiceField(choices=Status.choices)
    note = forms.CharField(required=False, max_length=500)


class FollowUpForm(forms.ModelForm):
    class Meta:
        model = Application
        fields = ["follow_up_on"]
        widgets = {"follow_up_on": forms.DateInput(attrs=DATE_ATTRS, format="%Y-%m-%d")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["follow_up_on"].input_formats = ["%Y-%m-%d", "%d/%m/%Y"]


class NotesForm(forms.ModelForm):
    class Meta:
        model = Application
        fields = ["personal_notes"]
        widgets = {
            "personal_notes": forms.Textarea(
                attrs={
                    "class": "input",
                    "rows": 8,
                    "placeholder": "Ce que tu retiens, les questions à poser, le nom du recruteur…",
                }
            )
        }


class EventForm(forms.ModelForm):
    class Meta:
        model = ActivityEvent
        fields = ["happened_on", "kind", "title", "detail"]
        widgets = {
            "happened_on": forms.DateInput(attrs=DATE_ATTRS, format="%Y-%m-%d"),
            "kind": forms.Select(attrs={"class": "input"}),
            "title": forms.TextInput(
                attrs={"class": "input", "placeholder": "Entretien téléphonique avec le recruteur"}
            ),
            "detail": forms.Textarea(attrs={"class": "input", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["happened_on"].initial = timezone.localdate()
        self.fields["happened_on"].input_formats = ["%Y-%m-%d", "%d/%m/%Y"]
        self.fields["kind"].initial = EventKind.NOTE


class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ["file", "kind", "label", "language", "is_primary"]
        widgets = {
            "file": forms.ClearableFileInput(attrs={"class": "input"}),
            "kind": forms.Select(attrs={"class": "input"}),
            "label": forms.TextInput(
                attrs={"class": "input", "placeholder": "Laisser vide pour reprendre le nom du fichier"}
            ),
            "language": forms.Select(attrs={"class": "input"}),
            "is_primary": forms.CheckboxInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["label"].required = False
        self.fields["kind"].initial = DocumentKind.CV
        self.fields["language"].required = False

    def clean(self):
        cleaned = super().clean()
        upload = cleaned.get("file")
        if upload and not cleaned.get("label"):
            cleaned["label"] = upload.name.rsplit("/", 1)[-1]
        return cleaned


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = ["name", "role", "email", "phone", "linkedin", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input"}),
            "role": forms.TextInput(attrs={"class": "input", "placeholder": "Recruteur, hiring manager…"}),
            "email": forms.EmailInput(attrs={"class": "input"}),
            "phone": forms.TextInput(attrs={"class": "input"}),
            "linkedin": forms.URLInput(attrs={"class": "input"}),
            "notes": forms.Textarea(attrs={"class": "input", "rows": 2}),
        }


SORT_CHOICES = [
    ("-score", "Compatibilité"),
    ("pipeline", "Étape du pipeline"),
    ("-applied_on", "Date de candidature"),
    ("follow_up_on", "Relance la plus proche"),
    ("company__name", "Société (A→Z)"),
    ("-updated_at", "Modifiée récemment"),
]


class ApplicationFilterForm(forms.Form):
    """Filter bar above the applications table. Every field is optional."""

    q = forms.CharField(
        required=False,
        label="Recherche",
        widget=forms.TextInput(
            attrs={
                "class": "input input--search",
                "placeholder": "Société, poste, lieu, note…",
                "autocomplete": "off",
                # The filter form's keyup trigger and the "/" shortcut both
                # look this element up by attribute.
                "data-search-input": "true",
            }
        ),
    )
    status = forms.MultipleChoiceField(required=False, choices=Status.choices)
    sector = forms.ChoiceField(
        required=False,
        choices=[("", "Tous les secteurs")] + list(Sector.choices),
        widget=forms.Select(attrs={"class": "input"}),
    )
    language = forms.ChoiceField(
        required=False,
        choices=[("", "Toutes les langues")] + list(Language.choices),
        widget=forms.Select(attrs={"class": "input"}),
    )
    work_mode = forms.ChoiceField(
        required=False,
        choices=[("", "Tous les modes")] + list(WorkMode.choices),
        widget=forms.Select(attrs={"class": "input"}),
    )
    scope = forms.ChoiceField(
        required=False,
        choices=[
            ("active", "En cours"),
            ("all", "Tout"),
            ("closed", "Clôturées"),
            ("discarded", "Écartées"),
        ],
        widget=forms.Select(attrs={"class": "input"}),
    )
    sort = forms.ChoiceField(
        required=False,
        choices=SORT_CHOICES,
        widget=forms.Select(attrs={"class": "input"}),
    )
