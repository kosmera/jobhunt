from django.contrib import admin

from accounts import conf
from accounts.models import Preferences, Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "display_name", "location", "onboarded_at", "premium_until"]
    search_fields = ["user__username", "user__email", "display_name"]
    autocomplete_fields = ["user"]

    def get_readonly_fields(self, request, obj=None):
        # Managing a local installation is not authority to grant a paid
        # subscription. The hosted billing administrator keeps this control.
        return ["premium_until"] if conf.is_local() else []


@admin.register(Preferences)
class PreferencesAdmin(admin.ModelAdmin):
    list_display = [
        "user", "stale_after_days", "follow_up_days", "search_radius_km", "default_cv_language",
    ]
    autocomplete_fields = ["user"]
