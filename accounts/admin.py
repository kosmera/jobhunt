from django.contrib import admin

from accounts.models import LaunchEmailJob, Preferences, Profile, SearchProfile


@admin.register(LaunchEmailJob)
class LaunchEmailJobAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "kind", "status", "attempts", "error_code", "next_attempt_at", "finished_at"]
    list_filter = ["kind", "status"]
    readonly_fields = [field.name for field in LaunchEmailJob._meta.fields]
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = [
        "user", "display_name", "location", "onboarded_at",
        "subscription_level", "premium_active", "premium_until",
        "launch_plan", "launch_email", "launch_consent_at",
    ]
    list_filter = ["subscription_level", "launch_plan", ("launch_consent_at", admin.EmptyFieldListFilter)]
    search_fields = ["user__username", "user__email", "display_name", "launch_email"]
    autocomplete_fields = ["user"]
    readonly_fields = ["launch_email", "launch_plan", "launch_consent_at"]

    @admin.display(boolean=True, description="Premium actif")
    def premium_active(self, obj):
        return obj.user.is_active and obj.is_premium


@admin.register(Preferences)
class PreferencesAdmin(admin.ModelAdmin):
    list_display = [
        "user", "stale_after_days", "follow_up_days", "search_radius_km", "default_cv_language",
    ]
    autocomplete_fields = ["user"]


@admin.register(SearchProfile)
class SearchProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "work_mode", "salary_min", "salary_period", "start_timeline", "updated_at"]
    search_fields = ["user__username", "user__email"]
    autocomplete_fields = ["user"]
