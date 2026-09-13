from django.contrib import admin

from accounts.models import Preferences, Profile, SearchProfile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = [
        "user", "display_name", "location", "onboarded_at",
        "subscription_level", "premium_active", "premium_until",
    ]
    list_filter = ["subscription_level"]
    search_fields = ["user__username", "user__email", "display_name"]
    autocomplete_fields = ["user"]

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
