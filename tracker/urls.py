"""URL map. French paths for pages, since they end up in the address bar."""

from django.urls import path

from tracker import views

app_name = "tracker"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("pipeline/", views.pipeline, name="pipeline"),
    path("candidatures/", views.application_list, name="application_list"),
    path("candidatures/nouvelle/", views.application_create, name="application_create"),
    path("candidatures/<int:pk>/", views.application_detail, name="application_detail"),
    path("candidatures/<int:pk>/modifier/", views.application_update, name="application_update"),
    path("candidatures/<int:pk>/supprimer/", views.application_delete, name="application_delete"),
    path("documents/", views.document_library, name="document_library"),
    path("analyse/", views.insights, name="insights"),

    # --- HTMX fragments ---------------------------------------------------
    path("hx/candidatures/rapide/", views.quick_create, name="quick_create"),
    path("hx/candidatures/<int:pk>/statut/", views.set_status, name="set_status"),
    path("hx/candidatures/<int:pk>/avancer/", views.advance_status, name="advance_status"),
    path("hx/candidatures/<int:pk>/relance/", views.set_follow_up, name="set_follow_up"),
    path("hx/candidatures/<int:pk>/relance/faite/", views.mark_followed_up, name="mark_followed_up"),
    path("hx/candidatures/<int:pk>/notes/", views.edit_notes, name="edit_notes"),
    path("hx/candidatures/<int:pk>/evenements/", views.add_event, name="add_event"),
    path("hx/evenements/<int:pk>/supprimer/", views.delete_event, name="delete_event"),
    path("hx/candidatures/<int:pk>/documents/", views.add_document, name="add_document"),
    path("hx/documents/", views.add_document, name="add_library_document"),
    path("hx/documents/<int:pk>/supprimer/", views.delete_document, name="delete_document"),
    path("hx/candidatures/<int:pk>/contacts/", views.add_contact, name="add_contact"),
    path("hx/contacts/<int:pk>/supprimer/", views.delete_contact, name="delete_contact"),
    path("hx/lacunes/<int:pk>/etat/", views.set_gap_status, name="set_gap_status"),
    path("hx/stats/", views.stats_bar, name="stats_bar"),
]
