"""Namespaced routes; the host selects their mounting prefix with include()."""

from django.urls import path

from jobhunt_ai import views

app_name = "jobhunt_ai"

urlpatterns = [
    path("api/scout/", views.api_scout_start, name="api_scout_start"),
    path("api/runs/<int:pk>/", views.api_run_status, name="api_run_status"),
    path("api/runs/<int:pk>/leads/", views.api_run_leads, name="api_run_leads"),
    path("", views.copilot, name="copilot"),
    path("profil/", views.profile_page, name="profile"),
    path("pistes/", views.leads_page, name="leads"),

    # --- Fragments HTMX ---------------------------------------------------
    path("hx/cv/analyser/", views.parse_cv, name="parse_cv"),
    path("hx/executions/<int:pk>/", views.run_status, name="run_status"),
    path("hx/candidatures/<int:pk>/evaluer/", views.evaluate, name="evaluate"),
    path("hx/candidatures/<int:pk>/generer-cv/", views.generate_cv, name="generate_cv"),
    path("hx/pistes/rechercher/", views.scout_start, name="scout_start"),
    path("hx/pistes/<int:pk>/importer/", views.lead_import, name="lead_import"),
    path("hx/pistes/<int:pk>/ecarter/", views.lead_dismiss, name="lead_dismiss"),
    path("hx/pistes/<int:pk>/restaurer/", views.lead_restore, name="lead_restore"),
    path("hx/profils/<int:pk>/principal/", views.profile_set_primary, name="profile_set_primary"),
    path("hx/profils/<int:pk>/supprimer/", views.profile_delete, name="profile_delete"),
]
