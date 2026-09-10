from django.contrib import admin
from django.urls import include, path

from jobhunt.landing import landing
from jobhunt.plugins import get_plugins

urlpatterns = [
    path("accueil/", landing, name="landing"),
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("", include("tracker.urls")),
]

# Chaque extension est montée sous son propre préfixe.
for plugin in get_plugins():
    urlpatterns.append(
        path(plugin.url_prefix, include(plugin.urls, namespace=plugin.name))
    )

# Les fichiers téléversés ne sont jamais servis tels quels, même en DEBUG :
# un document appartient à un profil et se télécharge par sa vue
# (``tracker:document_download``), qui vérifie le propriétaire.
