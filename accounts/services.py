"""Account operations shared by the views, the middleware and the tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.contrib import auth
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify

from accounts import conf
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
    LaunchPlan,
    Preferences,
    Profile,
    SalaryPeriod,
    SearchProfile,
    StartTimeline,
    WorkType,
    default_follow_up_days,
)
from rls import as_user
from tracker.models import Language, WorkMode

#: Username of the password-less account that the ownership migration creates
#: to hold data recorded before accounts existed.
LOCAL_USERNAME = "local"

#: The backend recorded on the session when a user is signed in without a
#: password. ``auth.login`` needs one; our backend also enforces verified
#: email on shared production sessions and keeps Django's permissions.
LOCAL_BACKEND = "accounts.backends.AccountsBackend"

#: The follow-up delay proposed to someone who needs a job soon, when the
#: preference still holds the environment default.
URGENT_FOLLOW_UP_DAYS = 7


def owned_or_404(queryset, user, **kwargs):
    """``get_object_or_404`` restricted to what ``user`` owns.

    Accepts a model or a queryset; the second form keeps ``select_related``
    and friends. Another account's row is indistinguishable from a missing one.
    """
    if not hasattr(queryset, "filter"):
        queryset = queryset._default_manager.all()
    return get_object_or_404(queryset, owner=user, **kwargs)


def profile_for(user) -> Profile:
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def preferences_for(user) -> Preferences:
    preferences, _ = Preferences.objects.get_or_create(user=user)
    return preferences


def search_profile_for(user) -> SearchProfile:
    search, _ = SearchProfile.objects.get_or_create(user=user)
    return search


def search_profile_or_blank(user) -> SearchProfile:
    """The account's search profile, or an unsaved blank one when the account
    never went through the questionnaire — a page that only reads it must not
    write a row."""
    return SearchProfile.objects.filter(user=user).first() or SearchProfile(user=user)


def has_premium(user) -> bool:
    """Read current Premium access; never trust a cached profile or form.

    The admin-managed level and expiration override the deployment default.
    No billing I/O occurs here; missing or disabled accounts have no access.
    """
    if not user or not user.is_authenticated or not user.pk:
        return False
    profile = Profile.objects.only("subscription_level", "premium_until").filter(
        user_id=user.pk, user__is_active=True,
    ).first()
    return bool(profile and profile.is_premium)


def ensure_local_admin(user) -> None:
    """The first account owns a trusted local installation.

    Repair older installations on sign-in or an existing session. Additional
    profiles and accounts-mode users are never automatically promoted. Only
    Django administration flags change; paid entitlement stays independent.
    """
    if (
        not conf.is_local() or not user or not user.is_authenticated
        or not user.is_active or not user.pk
        or (user.is_staff and user.is_superuser)
    ):
        return
    User = get_user_model()
    # auth.User is visible across accounts only while unbound. Restore the
    # caller's tenant afterwards; administrator status does not bypass RLS.
    with as_user(None):
        owner_id = User.objects.order_by("pk").values_list("pk", flat=True).first()
        if owner_id != user.pk:
            return
        promoted = User.objects.filter(pk=user.pk, is_active=True).update(
            is_staff=True, is_superuser=True
        )
    if promoted:
        user.is_staff = user.is_superuser = True


def unique_username(base: str) -> str:
    """A username derived from a display name, suffixed if already taken."""
    User = get_user_model()
    stem = slugify(base)[:140] or "profil"
    candidate, counter = stem, 2
    # Every account is a candidate for the clash: asked on behalf of nobody.
    with as_user(None):
        while User.objects.filter(username__iexact=candidate).exists():
            candidate = f"{stem}-{counter}"
            counter += 1
    return candidate


@transaction.atomic
def create_local_user(display_name: str):
    """A password-less account for local mode."""
    User = get_user_model()
    user = User(username=unique_username(display_name))
    user.set_unusable_password()
    user.save()
    return user


def complete_onboarding(
    user, *, display_name: str, headline: str = "", location: str = "", phone: str = ""
) -> Profile:
    """Only ``display_name`` is asked for; the rest fills in over time (the
    phone is never asked at onboarding, and stays empty until the settings
    page sets it)."""
    profile = profile_for(user)
    profile.display_name = display_name.strip()
    profile.headline = headline.strip()
    profile.location = location.strip()
    profile.phone = phone.strip()
    profile.onboarded_at = profile.onboarded_at or timezone.now()
    profile.save()
    preferences_for(user)
    return profile


def name_profile(user, display_name: str) -> Profile:
    """Set the display name only — the rest of onboarding is still to come."""
    profile = profile_for(user)
    profile.display_name = display_name.strip()
    profile.save()
    return profile


def _strings(value: Any, *, limit: int, allowed: set[str] | None = None) -> list[str]:
    """A bounded, de-duplicated list of non-empty strings out of a JSON value."""
    if not isinstance(value, list):
        return []
    kept: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            continue
        entry = entry.strip()[:MAX_LIST_ENTRY_LENGTH]
        if not entry or entry in kept or (allowed is not None and entry not in allowed):
            continue
        kept.append(entry)
        if len(kept) == limit:
            break
    return kept


def _choice(value: Any, choices: type[models.TextChoices]) -> str:
    return value if isinstance(value, str) and value in choices.values else ""


def search_profile_fields(answers: Mapping[str, Any]) -> dict[str, Any]:
    """Pure mapping answers → ``SearchProfile`` field values.

    Defensive on purpose: the answers travelled through a session. Unknown
    choice values become ``""`` or leave the lists, the lists are bounded,
    remote work clears the cities, "any industry" clears the sectors, "any"
    stands alone among the work types, a missing salary has no period.
    """
    work_mode = _choice(answers.get("work_mode"), WorkMode) or WorkMode.UNKNOWN
    any_industry = bool(answers.get("any_industry"))
    work_types = _strings(answers.get("work_types"), limit=len(WorkType.values), allowed=set(WorkType.values))
    if WorkType.ANY in work_types:
        work_types = [WorkType.ANY]
    salary_min = answers.get("salary_min")
    if not isinstance(salary_min, int) or isinstance(salary_min, bool) or salary_min <= 0:
        salary_min = None
    salary_period = _choice(answers.get("salary_period"), SalaryPeriod) if salary_min else ""
    if salary_period == "":
        salary_min = None
    return {
        "employment_status": _choice(answers.get("employment_status"), EmploymentStatus),
        "ai_tools_used": _choice(answers.get("ai_tools_used"), AiToolsUsed),
        "challenge": _choice(answers.get("challenge"), Challenge),
        "help_wanted": _strings(answers.get("help_wanted"), limit=len(HelpWanted.values), allowed=set(HelpWanted.values)),
        "job_titles": _strings(answers.get("job_titles"), limit=MAX_JOB_TITLES),
        "industries": [] if any_industry else _strings(answers.get("industries"), limit=len(Industry.values), allowed=set(Industry.values)),
        "any_industry": any_industry,
        "experience_level": _choice(answers.get("experience_level"), ExperienceLevel),
        "education_level": _choice(answers.get("education_level"), EducationLevel),
        "work_types": work_types,
        "work_mode": work_mode,
        "cities": [] if work_mode == WorkMode.REMOTE else _strings(answers.get("cities"), limit=MAX_CITIES),
        "salary_min": salary_min,
        "salary_period": salary_period,
        "start_timeline": _choice(answers.get("start_timeline"), StartTimeline),
    }


def finish_onboarding(user, answers: Mapping[str, Any]) -> Profile:
    """Turn the questionnaire into rows. One transaction: onboarded with everything, or not at all.

    ``complete_onboarding`` keeps its signature (the extension calls it) and
    stays idempotent; the headline and the home town it is given are the
    existing ones when the profile already had them, else the first title and
    the first city answered.
    """
    with transaction.atomic():
        profile = profile_for(user)
        preferences = preferences_for(user)
        fields = search_profile_fields(answers)
        titles: list[str] = fields["job_titles"]
        cities: list[str] = fields["cities"]
        display_name = answers.get("display_name")
        profile = complete_onboarding(
            user,
            display_name=display_name if isinstance(display_name, str) and display_name.strip() else profile.display_name,
            headline=profile.headline or (titles[0] if titles else ""),
            location=profile.location or (cities[0] if cities else ""),
            phone=profile.phone,
        )
        if conf.collect_launch_interest() and answers.get("launch_notify") is True:
            email = answers.get("launch_email")
            plan = answers.get("launch_plan")
            if not isinstance(email, str) or plan not in LaunchPlan.values:
                raise ValidationError("Un e-mail valide et une offre sont nécessaires pour être prévenu·e.")
            email = email.strip().lower()
            if not email:
                raise ValidationError("Un e-mail est nécessaire pour être prévenu·e.")
            email_field = Profile._meta.get_field("launch_email")
            assert isinstance(email_field, models.EmailField)
            email_field.clean(email, profile)
            profile.launch_email = email
            profile.launch_plan = plan
            profile.launch_consent_at = profile.launch_consent_at or timezone.now()
            profile.save(update_fields=["launch_email", "launch_plan", "launch_consent_at", "updated_at"])
            from accounts.email_delivery import enqueue_launch_emails

            enqueue_launch_emails(profile)
        radius = answers.get("search_radius_km")
        if isinstance(radius, int) and not isinstance(radius, bool) and 5 <= radius <= 300:
            preferences.search_radius_km = radius
        language = answers.get("cv_language")
        if isinstance(language, str) and language in Language.values:
            preferences.default_cv_language = language
        if (
            fields["employment_status"] == EmploymentStatus.UNEMPLOYED_URGENT
            and preferences.follow_up_days == default_follow_up_days()
        ):
            preferences.follow_up_days = min(preferences.follow_up_days, URGENT_FOLLOW_UP_DAYS)
        preferences.save()
        search = search_profile_for(user)
        for name, value in fields.items():
            setattr(search, name, value)
        search.full_clean()
        search.save()
    return profile


def sign_in_without_password(request, user) -> None:
    """Local mode only: the caller has already checked ``conf.is_local()``."""
    auth.login(request, user, backend=LOCAL_BACKEND)


def delete_account(request, user) -> None:
    """Remove the account and every row it owns.

    ``Application.company`` is ``PROTECT``: deleting the user directly would
    cascade into companies while applications still point at them and Django
    would refuse. Applications go first (taking events, contacts, documents
    and plugin rows with them), then the user (companies, platforms, gaps,
    library documents, profile, preferences). Signing out first keeps the
    local-mode middleware from re-authenticating the doomed account mid-way.
    """
    from tracker.models import Application

    auth.logout(request)
    with transaction.atomic():
        Application.objects.filter(owner=user).delete()
        user.delete()
