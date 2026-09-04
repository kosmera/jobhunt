"""Sign-up, sign-in, onboarding and settings forms."""

from __future__ import annotations

from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    SetPasswordForm,
)
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q

from accounts.models import Preferences, Profile
from accounts.services import complete_onboarding
from rls import as_user

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


class OnboardingForm(forms.Form):
    display_name = forms.CharField(
        label="Comment t'appeler ?",
        max_length=120,
        widget=text_input("Lionel", autofocus=True, autocomplete="name"),
    )
    headline = forms.CharField(
        label="Ce que tu cherches",
        max_length=200,
        required=False,
        widget=text_input("Ingénieur DevOps senior"),
        help_text="Une ligne, comme en tête de CV.",
    )
    location = forms.CharField(
        label="Ton point de départ",
        max_length=200,
        required=False,
        widget=text_input("Nivelles", autocomplete="address-level2"),
        help_text="La ville d'où se comptent les distances des offres. Modifiable dans les réglages.",
    )

    def clean_display_name(self) -> str:
        value = self.cleaned_data["display_name"].strip()
        if not value:
            raise forms.ValidationError("Il faut bien un nom.")
        return value

    def apply(self, user) -> Profile:
        return complete_onboarding(user, **self.cleaned_data)


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
        # The account exists but nobody is signed in as it yet: its profile
        # is completed on its own behalf, the only rows the database accepts.
        with as_user(user):
            complete_onboarding(user, display_name=self.cleaned_data["display_name"])
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
