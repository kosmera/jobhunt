from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from jobhunt.landing import landing

urlpatterns = [
    path("accueil/", landing, name="landing"),
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("", include("tracker.urls")),
]

# Le copilote IA, sous son propre préfixe (l'espace de noms ``jobhunt_ai``
# vient de son module d'URLs). Absent quand COPILOT_ENABLED est à 0.
if settings.COPILOT_ENABLED:
    urlpatterns.append(path("copilote/", include("jobhunt_ai.urls")))

# Les fichiers téléversés ne sont jamais servis tels quels, même en DEBUG :
# un document appartient à un profil et se télécharge par sa vue
# (``tracker:document_download``), qui vérifie le propriétaire.
