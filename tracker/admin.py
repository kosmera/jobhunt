"""Admin registration — a fallback for bulk edits the UI does not cover."""

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
    list_display = ["title", "company", "status", "score", "applied_on", "follow_up_on"]
    list_filter = ["status", "cv_language", "company__sector", "work_mode"]
    search_fields = ["title", "company__name", "location", "summary", "personal_notes"]
    date_hierarchy = "discovered_on"
    autocomplete_fields = ["company"]
    inlines = [DocumentInline, EventInline, ContactInline]
    readonly_fields = ["slug", "created_at", "updated_at"]


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ["name", "sector", "location"]
    list_filter = ["sector"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display = ["name", "is_lead", "last_checked"]
    list_filter = ["is_lead"]
    search_fields = ["name", "searched_for", "outcome"]


@admin.register(SkillGap)
class SkillGapAdmin(admin.ModelAdmin):
    list_display = ["name", "demand_count", "status", "position"]
    list_editable = ["status", "position"]
    list_filter = ["status"]


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ["label", "kind", "application", "language", "is_primary"]
    list_filter = ["kind", "language", "is_primary"]
    search_fields = ["label"]


@admin.register(ActivityEvent)
class ActivityEventAdmin(admin.ModelAdmin):
    list_display = ["happened_on", "application", "kind", "title"]
    list_filter = ["kind"]
    date_hierarchy = "happened_on"


admin.site.site_header = "JobHunt — administration"
admin.site.site_title = "JobHunt"
admin.site.index_title = "Données"
