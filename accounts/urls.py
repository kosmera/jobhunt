"""Account URLs. French paths, like the rest of the application."""

from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("bienvenue/", views.onboarding, name="onboarding"),
    path("connexion/", views.login_view, name="login"),
    path("inscription/", views.signup, name="signup"),
    path("deconnexion/", views.logout_view, name="logout"),
    path("reglages/", views.settings_view, name="settings"),
    path("reglages/<slug:section>/", views.settings_view, name="settings_section"),
]
