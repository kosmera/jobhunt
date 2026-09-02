from django.contrib import admin

from accounts.models import Preferences, Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "display_name", "location", "onboarded_at"]
    search_fields = ["user__username", "user__email", "display_name"]
    autocomplete_fields = ["user"]


@admin.register(Preferences)
class PreferencesAdmin(admin.ModelAdmin):
    list_display = [
        "user", "stale_after_days", "follow_up_days", "search_radius_km", "default_cv_language",
    ]
    autocomplete_fields = ["user"]
