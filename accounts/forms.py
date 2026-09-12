"""Sign-up, sign-in and settings forms; the questionnaire's own are in ``accounts.onboarding.forms``."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    SetPasswordForm,
)
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q

from accounts.models import (
    MAX_CITIES,
    MAX_JOB_TITLES,
    MAX_LIST_ENTRY_LENGTH,
    AiToolsUsed,
    Challenge,
    EducationLevel,
    EmploymentStatus,
    ExperienceLevel,
    HelpWanted,
    Industry,
    Preferences,
    Profile,
    SalaryPeriod,
    SearchProfile,
    StartTimeline,
    WorkType,
)
from accounts.onboarding.flow import SALARY_BOUNDS
from accounts.services import name_profile, search_profile_fields
from rls import as_user
from tracker.models import WorkMode

INPUT = {"class": "input"}

#: ``auth.User.username`` is 150 characters; e-mails double as usernames.
EMAIL_MAX_LENGTH = 150

#: What ``AUTH_PASSWORD_VALIDATORS`` actually enforces, in house voice —
#: Django's own help text says "vous".
PASSWORD_HELP = "Huit caractères ou plus, pas un mot du dictionnaire ni ton nom."


def text_input(placeholder: str = "", autofocus: bool = False, **extra) -> forms.TextInput:
    attrs = {"class": "input", "placeholder": placeholder, **extra}
    if autofocus:
        attrs["autofocus"] = "autofocus"
    return forms.TextInput(attrs=attrs)


def chips_input(placeholder: str, *, source: str, name: str, limit: int) -> forms.TextInput:
    """The text field that static/js/fields.js turns into a combobox.

    ``source`` is the id of the ``json_script`` holding the suggestions,
    ``name`` the name of the boxes the chosen chips carry, ``limit`` how many
    the form accepts.
    """
    return forms.TextInput(
        attrs={
            "class": "input",
            "placeholder": placeholder,
            "autocomplete": "off",
            "data-chips": source,
            "data-chips-name": name,
            "data-chips-max": str(limit),
        }
    )


def chips_text_field(
    label: str, placeholder: str, *, source: str, name: str, limit: int, plural: str, help_text: str
) -> forms.CharField:
    """The comma-separated fallback of a chips field, bounded before ``clean()`` runs.

    ``limit`` entries of ``MAX_LIST_ENTRY_LENGTH`` characters plus their
    separators is all a legitimate submission can hold; anything longer is
    refused by the field itself, so ``merge_entries`` never has to walk a
    body of megabytes.
    """
    return forms.CharField(
        label=label,
        required=False,
        max_length=(limit + 1) * (MAX_LIST_ENTRY_LENGTH + 2),
        widget=chips_input(placeholder, source=source, name=name, limit=limit),
        help_text=help_text,
        error_messages={"max_length": f"C'est trop long pour un champ : {limit} {plural} au maximum, séparés par des virgules."},
    )


def getlist(data: Any, name: str) -> list[str]:
    """Every value of ``name``: a ``QueryDict`` has ``getlist``, a plain dict may hold a list."""
    if data is None:
        return []
    if hasattr(data, "getlist"):
        return [str(v) for v in data.getlist(name)]
    value = data.get(name)
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]


def merge_entries(picked: Iterable[str], text: str, *, limit: int, what: str, plural: str) -> list[str]:
    """Chips plus the comma-separated fallback, stripped, de-duplicated case-insensitively."""
    entries: list[str] = []
    seen: set[str] = set()
    for raw in [*picked, *text.split(",")]:
        entry = " ".join(raw.split())
        if not entry:
            continue
        if len(entry) > MAX_LIST_ENTRY_LENGTH:
            raise forms.ValidationError(f"{what} fait {MAX_LIST_ENTRY_LENGTH} caractères au maximum.")
        if not entry.isprintable():
            # The chips bypass the CharField's own NUL check; PostgreSQL would refuse the row.
            raise forms.ValidationError(f"{what} contient un caractère interdit.")
        if entry.casefold() in seen:
            continue
        seen.add(entry.casefold())
        entries.append(entry)
        if len(entries) > limit:
            raise forms.ValidationError(f"{limit} {plural}, c'est déjà beaucoup.")
    return entries


def submitted(data: Any, name: str) -> list[str]:
    """The chips of a submission, so a page shown again with an error keeps them."""
    return [" ".join(value.split()) for value in getlist(data, name) if value.strip()]


def email_is_taken(email: str, exclude_pk=None) -> bool:
    """Case-insensitive, and against usernames too: accounts mode stores the
    e-mail as the username."""
    queryset = User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email))
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    # A question about every account, not the signed-in one: asked on behalf
    # of nobody, which is how the user table answers for all its rows.
    with as_user(None):
        return queryset.exists()


class LoginForm(AuthenticationForm):
    """Accounts mode. An e-mail is resolved to the account that holds it —
    whether as username (sign-up accounts) or only in ``email`` (accounts
    created locally or with ``createsuperuser``); a plain username still works."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "E-mail ou mot de passe incorrect.",
        "inactive": "Ce compte est désactivé.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "E-mail ou identifiant"
        self.fields["username"].widget = text_input(
            "toi@example.org", autofocus=True, autocomplete="username"
        )
        self.fields["password"].widget = forms.PasswordInput(
            attrs={"class": "input", "autocomplete": "current-password"}
        )

    def clean_username(self) -> str:
        value = self.cleaned_data["username"].strip()
        if "@" not in value:
            return value
        value = value.lower()
        if User.objects.filter(username__iexact=value).exists():
            return value
        holders = list(User.objects.filter(email__iexact=value).values_list("username", flat=True)[:2])
        return holders[0] if len(holders) == 1 else value


class SignupForm(forms.Form):
    display_name = forms.CharField(
        label="Comment t'appeler ?",
        max_length=120,
        widget=text_input("Lionel", autofocus=True, autocomplete="name"),
    )
    email = forms.EmailField(
        label="E-mail",
        max_length=EMAIL_MAX_LENGTH,
        widget=forms.EmailInput(
            attrs={"class": "input", "placeholder": "toi@example.org", "autocomplete": "email"}
        ),
        help_text="C'est ton identifiant de connexion.",
    )
    password1 = forms.CharField(
        label="Mot de passe",
        widget=forms.PasswordInput(attrs={"class": "input", "autocomplete": "new-password"}),
        help_text=PASSWORD_HELP,
    )
    password2 = forms.CharField(
        label="Mot de passe, encore",
        widget=forms.PasswordInput(attrs={"class": "input", "autocomplete": "new-password"}),
    )

    def clean_display_name(self) -> str:
        value = self.cleaned_data["display_name"].strip()
        if not value:
            raise forms.ValidationError("Il faut bien un nom.")
        return value

    def clean_email(self) -> str:
        email = self.cleaned_data["email"].strip().lower()
        if email_is_taken(email):
            raise forms.ValidationError("Un compte existe déjà avec cet e-mail.")
        return email

    def clean_password2(self) -> str | None:
        first = self.cleaned_data.get("password1")
        second = self.cleaned_data.get("password2")
        if first and second and first != second:
            raise forms.ValidationError("Les deux mots de passe ne correspondent pas.")
        return second

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        password = cleaned.get("password2")
        if password:
            probe = User(username=cleaned.get("email", ""), email=cleaned.get("email", ""))
            try:
                validate_password(password, probe)
            except forms.ValidationError as exc:
                self.add_error("password2", exc)
        return cleaned

    def save(self):
        email = self.cleaned_data["email"]
        user = User.objects.create_user(
            username=email, email=email, password=self.cleaned_data["password1"]
        )
        # The account exists but nobody is signed in as it yet: its profile is
        # named on its own behalf, the only rows the database accepts. The
        # questionnaire completes it afterwards.
        with as_user(user):
            name_profile(user, self.cleaned_data["display_name"])
        return user


class ProfileForm(forms.ModelForm):
    email = forms.EmailField(
        label="E-mail",
        required=False,
        max_length=EMAIL_MAX_LENGTH,
        widget=forms.EmailInput(attrs={"class": "input", "autocomplete": "email"}),
        help_text="Identifiant de connexion en mode comptes.",
    )

    class Meta:
        model = Profile
        fields = ["display_name", "headline", "location", "phone"]
        widgets = {
            "display_name": text_input("Lionel", autocomplete="name"),
            "headline": text_input("Ingénieur DevOps senior"),
            "location": text_input("Nivelles", autocomplete="address-level2"),
            "phone": text_input("+32 470 12 34 56", autocomplete="tel"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["display_name"].required = True
        self.fields["email"].initial = self.instance.user.email

    def clean_display_name(self) -> str:
        value = self.cleaned_data["display_name"].strip()
        if not value:
            raise forms.ValidationError("Il faut bien un nom.")
        return value

    def clean_email(self) -> str:
        email = self.cleaned_data["email"].strip().lower()
        if email and email_is_taken(email, exclude_pk=self.instance.user_id):
            raise forms.ValidationError("Un autre compte utilise déjà cet e-mail.")
        return email

    def save(self, commit=True):
        profile = super().save(commit=commit)
        user = profile.user
        previous = user.email
        email = self.cleaned_data["email"]
        if email != previous:
            user.email = email
            # An account signed up with its e-mail as username keeps the two in step,
            # otherwise the old address would stay the login and the new one would not work.
            if previous and user.username.lower() == previous.lower() and email:
                user.username = email
            if commit:
                user.save(update_fields=["email", "username"])
        return profile


class PreferencesForm(forms.ModelForm):
    class Meta:
        model = Preferences
        fields = ["stale_after_days", "follow_up_days", "search_radius_km", "default_cv_language"]
        widgets = {
            "stale_after_days": forms.NumberInput(attrs={"class": "input", "min": 1, "max": 365}),
            "follow_up_days": forms.NumberInput(attrs={"class": "input", "min": 1, "max": 365}),
            "search_radius_km": forms.NumberInput(attrs={"class": "input", "min": 5, "max": 300}),
            "default_cv_language": forms.Select(attrs={"class": "input"}),
        }


class ChipCheckboxes(forms.CheckboxSelectMultiple):
    """Checkboxes drawn as toggle chips (``.chip--toggle``).

    An ``exclusive`` value clears the others when checked (fields.js) and is
    stored alone: « tous secteurs », « peu importe ». The questionnaire draws
    « peu importe » as a check-all card instead; both store ``["any"]``.
    """

    template_name = "accounts/widgets/chip_checkboxes.html"

    def __init__(self, *, exclusive: Iterable[str] = (), attrs=None, choices=()):
        super().__init__({"class": "visually-hidden", **(attrs or {})}, choices)
        self.exclusive = frozenset(exclusive)

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        if str(value) in self.exclusive:
            option["attrs"]["data-exclusive"] = True
        return option


#: The pseudo-sector that stands for ``SearchProfile.any_industry`` in the form.
ANY_INDUSTRY = "all"

SELECT = {"class": "input"}
NO_ANSWER = ("", "Pas de réponse")


def _select(choices: Sequence[tuple[str, Any]], *, label: str) -> forms.ChoiceField:
    """A single choice, or none: the model leaves every one of them blank."""
    return forms.ChoiceField(
        label=label,
        required=False,
        choices=[NO_ANSWER, *((value, str(text)) for value, text in choices)],
        widget=forms.Select(attrs=SELECT),
        error_messages={"invalid_choice": "Choisis une réponse de la liste."},
    )


class SearchProfileForm(forms.Form):
    """Every answer of the questionnaire, editable in one go on the settings page.

    A plain ``Form`` on purpose: the chips fields read the request the way
    the questionnaire's do (one box per chip, a comma-separated text
    fallback), and ``save()`` runs the cleaned answers through
    ``search_profile_fields`` so the settings keep the invariants the
    questionnaire established — « peu importe » alone among the contracts,
    « tous secteurs » clearing the sectors, remote work clearing the cities,
    a salary going with its period. What the questionnaire insists on (a
    title, a contract type) stays required here; the context answers
    (situation, difficulty, expectations, AI tools) may be left blank.
    """

    titles_text = chips_text_field(
        "Postes visés", "Développeur web, comptable, infirmier…",
        source="titles-suggestions", name="titles", limit=MAX_JOB_TITLES, plural="intitulés",
        help_text="Plusieurs postes ? Sépare-les par des virgules.",
    )
    industries = forms.MultipleChoiceField(
        label="Secteurs",
        required=False,
        choices=[*Industry.choices, (ANY_INDUSTRY, "Tous secteurs")],
        widget=ChipCheckboxes(exclusive={ANY_INDUSTRY}),
        error_messages={"invalid_choice": "Choisis des secteurs de la liste."},
    )
    experience_level = _select(ExperienceLevel.choices, label="Niveau d'expérience")
    education_level = _select(EducationLevel.choices, label="Formation")
    work_types = forms.MultipleChoiceField(
        label="Types de contrat",
        choices=WorkType.choices,
        widget=ChipCheckboxes(exclusive={WorkType.ANY}),
        error_messages={
            "required": "Choisis au moins un type de contrat.",
            "invalid_choice": "Choisis des contrats de la liste.",
        },
    )
    work_mode = _select(
        [(value, label) for value, label in WorkMode.choices if value != WorkMode.UNKNOWN],
        label="Mode de travail",
    )
    cities_text = chips_text_field(
        "Villes où travailler sur place", "Nivelles",
        source="cities-suggestions", name="cities", limit=MAX_CITIES, plural="villes",
        help_text="Plusieurs villes ? Sépare-les par des virgules. En télétravail, la liste est vidée ; "
        "les distances, elles, partent du point de départ du profil.",
    )
    salary_period = forms.ChoiceField(
        required=False,
        choices=SalaryPeriod.choices,
        widget=forms.RadioSelect,
        initial=SalaryPeriod.MONTH,
        error_messages={"invalid_choice": "Choisis une période."},
    )
    salary_min = forms.IntegerField(
        label="Salaire minimum (brut)",
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "input", "inputmode": "numeric", "min": 1}),
        help_text="Un repère pour comparer les offres, rien d'autre. Laisse vide pour ne pas le dire.",
        error_messages={"invalid": "Un montant entier, ou rien.", "min_value": "Un montant au-dessus de zéro, ou rien."},
    )
    start_timeline = _select(StartTimeline.choices, label="Horizon de départ")
    employment_status = _select(EmploymentStatus.choices, label="Situation")
    challenge = _select(Challenge.choices, label="Principale difficulté")
    help_wanted = forms.MultipleChoiceField(
        label="Ce que tu attends de tonjobidéal",
        required=False,
        choices=HelpWanted.choices,
        widget=ChipCheckboxes(),
        error_messages={"invalid_choice": "Choisis des attentes de la liste."},
    )
    ai_tools_used = _select(AiToolsUsed.choices, label="Outils d'IA déjà essayés")

    def __init__(self, data=None, *, instance: SearchProfile, **kwargs):
        kwargs.setdefault("initial", self.initial_from(instance))
        super().__init__(data, **kwargs)
        self.instance = instance
        # The chips shown: what was submitted (a page shown again with an
        # error keeps them), else what the row holds.
        self.titles: list[str] = submitted(self.data, "titles") if self.is_bound else list(instance.job_titles)
        self.cities: list[str] = submitted(self.data, "cities") if self.is_bound else list(instance.cities)

    @staticmethod
    def initial_from(instance: SearchProfile) -> dict[str, Any]:
        return {
            "industries": [ANY_INDUSTRY] if instance.any_industry else list(instance.industries),
            "experience_level": instance.experience_level,
            "education_level": instance.education_level,
            "work_types": list(instance.work_types),
            "work_mode": "" if instance.work_mode == WorkMode.UNKNOWN else instance.work_mode,
            "salary_period": instance.salary_period or SalaryPeriod.MONTH,
            "salary_min": instance.salary_min,
            "start_timeline": instance.start_timeline,
            "employment_status": instance.employment_status,
            "challenge": instance.challenge,
            "help_wanted": list(instance.help_wanted),
            "ai_tools_used": instance.ai_tools_used,
        }

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        # A text field the CharField already refused (too long) is not merged:
        # its own message is the one to act on.
        if "titles_text" not in self.errors:
            try:
                self.titles = merge_entries(
                    getlist(self.data, "titles"), cleaned.get("titles_text", ""),
                    limit=MAX_JOB_TITLES, what="Un intitulé", plural="intitulés",
                )
            except forms.ValidationError as exc:
                self.add_error("titles_text", exc)
            else:
                if not self.titles:
                    self.add_error("titles_text", "Indique au moins un poste.")
        if "cities_text" not in self.errors:
            try:
                self.cities = merge_entries(
                    getlist(self.data, "cities"), cleaned.get("cities_text", ""),
                    limit=MAX_CITIES, what="Une ville", plural="villes",
                )
            except forms.ValidationError as exc:
                self.add_error("cities_text", exc)
        amount = cleaned.get("salary_min")
        period = cleaned.get("salary_period")
        if amount is not None and "salary_period" not in self.errors:
            if period not in SALARY_BOUNDS:
                self.add_error("salary_period", "Choisis une période.")
            elif amount > SALARY_BOUNDS[period][0]:
                self.add_error("salary_min", "Ce montant semble hors limites pour cette période.")
        return cleaned

    def answers(self) -> dict[str, Any]:
        """The cleaned form as the questionnaire's answers, for ``search_profile_fields``."""
        cleaned = self.cleaned_data
        sectors = list(cleaned.get("industries") or [])
        any_industry = ANY_INDUSTRY in sectors
        salary = cleaned.get("salary_min")
        return {
            "employment_status": cleaned.get("employment_status", ""),
            "ai_tools_used": cleaned.get("ai_tools_used", ""),
            "challenge": cleaned.get("challenge", ""),
            "help_wanted": list(cleaned.get("help_wanted") or []),
            "job_titles": self.titles,
            "industries": [] if any_industry else sectors,
            "any_industry": any_industry,
            "experience_level": cleaned.get("experience_level", ""),
            "education_level": cleaned.get("education_level", ""),
            "work_types": list(cleaned.get("work_types") or []),
            "work_mode": cleaned.get("work_mode") or WorkMode.UNKNOWN,
            "cities": self.cities,
            "salary_min": salary,
            "salary_period": cleaned.get("salary_period", "") if salary is not None else "",
            "start_timeline": cleaned.get("start_timeline", ""),
        }

    def save(self) -> SearchProfile:
        """Write the row — created on the first save of an account that skipped the questionnaire."""
        search = self.instance
        for name, value in search_profile_fields(self.answers()).items():
            setattr(search, name, value)
        search.full_clean()
        search.save()
        return search


def _style_password_form(form):
    for field in form.fields.values():
        field.widget.attrs.setdefault("class", "input")
    form.fields["new_password1"].help_text = PASSWORD_HELP
    return form


class StyledPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style_password_form(self)


class StyledSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style_password_form(self)


def password_form(user, data=None):
    """Set a first password, or change the existing one."""
    if user.has_usable_password():
        return StyledPasswordChangeForm(user, data)
    return StyledSetPasswordForm(user, data)


class DeleteAccountForm(forms.Form):
    confirm = forms.CharField(
        label="Recopie ton nom affiché pour confirmer",
        widget=text_input(autocomplete="off"),
    )

    def __init__(self, *args, expected: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.expected = expected

    def clean_confirm(self) -> str:
        value = self.cleaned_data["confirm"].strip()
        if value.casefold() != self.expected.strip().casefold():
            raise forms.ValidationError("Le nom ne correspond pas : rien n'a été supprimé.")
        return value
