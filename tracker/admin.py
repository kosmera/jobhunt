"""Admin registration — a fallback for bulk edits the UI does not cover."""

from django import forms
from django.contrib import admin

from tracker.models import (
    ActivityEvent,
    Application,
    Company,
    Contact,
    Document,
    Platform,
    SkillGap,
)


def _same_owner(cleaned, related_name: str, message: str):
    """The model raises on a mismatch; the admin form should say so instead."""
    owner = cleaned.get("owner")
    related = cleaned.get(related_name)
    if owner and related and related.owner_id != owner.pk:
        raise forms.ValidationError({related_name: message})


class ApplicationAdminForm(forms.ModelForm):
    class Meta:
        model = Application
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        _same_owner(cleaned, "company", "Cette société appartient à un autre profil.")
        return cleaned


class DocumentAdminForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        _same_owner(cleaned, "application", "Cette candidature appartient à un autre profil.")
        return cleaned


class DocumentInline(admin.TabularInline):
    model = Document
    extra = 0
    fields = ["kind", "label", "file", "language", "is_primary"]


class EventInline(admin.TabularInline):
    model = ActivityEvent
    extra = 0
    fields = ["happened_on", "kind", "title", "detail"]
    ordering = ["-happened_on"]


class ContactInline(admin.TabularInline):
    model = Contact
    extra = 0


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    form = ApplicationAdminForm
    list_display = ["title", "company", "owner", "status", "score", "applied_on", "follow_up_on"]
    list_filter = ["owner", "status", "cv_language", "company__sector", "work_mode"]
    search_fields = ["title", "company__name", "location", "summary", "personal_notes"]
    date_hierarchy = "discovered_on"
    autocomplete_fields = ["company", "owner"]
    inlines = [DocumentInline, EventInline, ContactInline]
    readonly_fields = ["slug", "created_at", "updated_at"]


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "sector", "location"]
    list_filter = ["owner", "sector"]
    search_fields = ["name"]
    autocomplete_fields = ["owner"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "is_lead", "last_checked"]
    list_filter = ["owner", "is_lead"]
    search_fields = ["name", "searched_for", "outcome"]
    autocomplete_fields = ["owner"]


@admin.register(SkillGap)
class SkillGapAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "demand_count", "status", "position"]
    list_editable = ["status", "position"]
    list_filter = ["owner", "status"]
    autocomplete_fields = ["owner"]


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    form = DocumentAdminForm
    list_display = ["label", "kind", "owner", "application", "language", "is_primary"]
    list_filter = ["owner", "kind", "language", "is_primary"]
    search_fields = ["label"]
    autocomplete_fields = ["owner", "application"]


@admin.register(ActivityEvent)
class ActivityEventAdmin(admin.ModelAdmin):
    list_display = ["happened_on", "application", "kind", "title"]
    list_filter = ["kind"]
    date_hierarchy = "happened_on"


admin.site.site_header = "JobHunt — administration"
admin.site.site_title = "JobHunt"
admin.site.index_title = "Données"
