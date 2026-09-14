"""Account URLs. French paths, like the rest of the application."""

from django.urls import path

from accounts import views
from accounts.onboarding import views as onboarding_views

app_name = "accounts"

urlpatterns = [
    # The questionnaire: its entry keeps the name the middleware and the landing
    # page reverse; every screen has its own French segment under it.
    path("bienvenue/", onboarding_views.onboarding, name="onboarding"),
    path("bienvenue/cv/analyse/", onboarding_views.onboarding_cv_status, name="onboarding_cv_status"),
    path("bienvenue/<slug:slug>/", onboarding_views.onboarding_step, name="onboarding_step"),
    path("connexion/", views.login_view, name="login"),
    path("connexion/email/", views.email_link_sent, name="email_link_sent"),
    path("connexion/email/renvoyer/", views.email_link_resend, name="email_link_resend"),
    path("connexion/lien/<str:token>/", views.email_link_confirm, name="email_link_confirm"),
    path("inscription/", views.signup, name="signup"),
    path("deconnexion/", views.logout_view, name="logout"),
    path("reglages/", views.settings_view, name="settings"),
    path("reglages/<slug:section>/", views.settings_view, name="settings_section"),
]
