# JobHunt — suivi de candidatures

Remplace `00_Suivi_candidatures.xlsx` par une petite application Django + HTMX.
Le tableur, les dossiers `Offres/` et les CV de `CV_base/` ont été repris dans une
base SQLite ; les fichiers d'origine sont **inchangés**, les documents en ont été
copiés dans `media/`.

## Démarrer

Les dépendances sont gérées par [uv](https://docs.astral.sh/uv/). La première
fois, ou après un `git pull` :

```bash
uv sync
```

Ça crée `.venv/` et y installe exactement ce que décrit `uv.lock` — rien à
activer, rien à installer à la main.

```bash
uv run manage.py runserver
```

Puis <http://localhost:8000>. En local il n'y a ni mot de passe ni écran de
connexion : la première visite demande un nom et un point de départ, et c'est
ton profil. Voir [Comptes et profil](#comptes-et-profil).

Ni `makemigrations` ni `migrate` à lancer à la main : en développement,
`runserver` écrit la migration d'un modèle modifié, puis applique tout ce qui
est en attente avant de servir — et de nouveau à chaque rechargement, donc
aussi après un `git pull` ou l'installation d'une extension. Une seule
réserve : quand Django devrait te poser une question (un renommage possible,
un champ obligatoire sans valeur par défaut — la mauvaise réponse perd des
données), rien n'est généré et le démarrage te renvoie à
`manage.py makemigrations`. `JOBHUNT_AUTO_MIGRATE=0` rend à `runserver` son
comportement d'origine ; en production, `migrate` reste une étape explicite du
déploiement. L'automatisme ne s'applique qu'à une base locale : un
`runserver` de développement pointé sur un serveur distant (voir
[Base de données](#base-de-données)) ne génère ni n'applique rien de
lui-même — `JOBHUNT_AUTO_MIGRATE=1` pour le forcer en connaissance de cause.

Pour l'administration Django (`/admin`, édition brute des tables), il faut un
compte avec le drapeau *staff* :

```bash
uv run manage.py createsuperuser
```

## Ce qu'il y a dans l'application

| Page | À quoi elle sert |
| --- | --- |
| **Tableau de bord** | Ce qu'il faut traiter aujourd'hui : relances échues, candidatures sans nouvelles, dossiers prêts à partir classés par compatibilité. |
| **Pipeline** | Les candidatures en colonnes, du vivier à l'offre. Glisse une carte pour changer son statut. |
| **Candidatures** | Le tableau complet, avec recherche instantanée et filtres. La recherche porte aussi sur les analyses et tes notes : tape `Terraform` pour retrouver les trois offres où il te manque. |
| **Documents** | Les 4 CV génériques d'un côté, les CV adaptés rattachés à leur offre de l'autre. |
| **Analyse** | Les lacunes à combler, les plateformes explorées, les offres écartées (récupérables en un clic), et un nuage compatibilité × distance. |
| **Réglages** | Ton profil (nom, titre, point de départ, e-mail), tes préférences (délais de relance et de signalement, rayon, langue de CV), ton mot de passe, et la suppression du compte. Accessible depuis ton nom, en bas de la barre latérale. |

### Automatismes

Le suivi tient les dates tout seul, pour que tu n'aies qu'un geste à faire :

- passer une candidature en « envoyée » date l'envoi du jour et programme une
  relance à +10 jours ;
- « Relance faite » consigne la relance dans le fil et repousse la suivante ;
- clôturer une candidature date la clôture et retire la relance ;
- au-delà de 14 jours sans nouvelles, une candidature envoyée remonte dans
  « À traiter maintenant ».

Ces deux délais se règlent dans **Réglages**, profil par profil ; les variables
d'environnement de `.env.example` ne fixent que les valeurs proposées à un
nouveau profil.

### Raccourcis

- `n` — ajouter une offre
- `/` — aller à la recherche (page Candidatures)

## Ce qui a été repris du tableur

| Onglet d'origine | Devenu |
| --- | --- |
| Candidatures (10 lignes) | Candidatures actives |
| Offres écartées (14 lignes) | Candidatures au statut « Écartée », avec leur raison |
| Plateformes explorées (11 lignes) | Page Analyse |
| Lacunes à combler (7 lignes) | Page Analyse, avec un état à travailler / en cours / comblée |
| `Offres/NN_.../notes.md` | Les trois sections (points forts, points faibles, angle d'attaque) en champs distincts |
| `Offres/NN_.../annonce_originale.md` | Texte de l'annonce, conservé sur la fiche |
| `Offres/NN_.../CV_*.docx` et `CV_base/*.docx` | Documents, copiés dans `media/` |

Les offres écartées sont dans la même table que les autres : celles que le
LISEZMOI signalait comme méritant un second regard — Odoo, Vertuoza, 4G Clinical —
se reprennent depuis la page Analyse, bouton « Reprendre ».

### Relancer l'import

La commande est idempotente : elle met à jour ce qui existe et ne duplique rien.

```bash
uv run manage.py import_legacy
```

Options : `--workbook` (autre classeur), `--root` (autre dossier `Offres/`),
`--skip-files` (données seules, sans copier les documents), `--user`
(identifiant du profil qui reçoit les données — facultatif tant qu'il n'y a
qu'un profil).

Le dossier personnel — `00_Suivi_candidatures.xlsx`, `00_LISEZMOI.md`, `Offres/`
et `CV_base/` — est **hors du dépôt**, avec `db.sqlite3` et `media/`. C'est
délibéré : le code est publiable, les CV et les analyses ne le sont pas. Sur un
clone neuf il n'y a donc rien à importer ; l'application démarre sur une base
vide, et chaque page a son état vide.

## Comptes et profil

Chaque ligne du suivi — candidature, société, plateforme, lacune, document —
appartient à un profil, et chaque page ne montre que le sien. Deux façons
d'entrer, choisies par `JOBHUNT_AUTH_MODE` :

| Mode | Quand | Comment on entre |
| --- | --- | --- |
| `local` (défaut si `JOBHUNT_DEBUG=1`) | ta machine | Aucun mot de passe. Pas de profil → page **Bienvenue** (nom, titre, point de départ). Un profil → connecté d'office. Plusieurs → on choisit dans une liste, et « Changer de profil » en bas de la barre latérale y ramène. |
| `accounts` (défaut si `JOBHUNT_DEBUG=0`) | une instance partagée | **Connexion** (e-mail ou identifiant + mot de passe) et **Inscription**. `JOBHUNT_SIGNUP_OPEN=0` ferme les inscriptions ; un compte créé autrement (`createsuperuser`) passe par la page Bienvenue à sa première visite. |

### Reprise d'une base existante

La migration qui a introduit les comptes rattache tout ce qui existait déjà à
un profil : le seul compte présent s'il y en a un, sinon un compte `local`
sans mot de passe créé pour l'occasion. À la visite suivante, la page
Bienvenue annonce combien de candidatures attendent et complète ce compte —
rien n'est à ressaisir.

### Passer d'une base locale au mode comptes

Un profil local n'a pas de mot de passe. Avant de basculer `JOBHUNT_AUTH_MODE`
sur `accounts`, ouvre **Réglages**, renseigne un e-mail et définis un mot de
passe ; sinon, en ligne de commande :

```bash
uv run manage.py changepassword <identifiant>
```

L'identifiant est affiché dans Réglages (`local` pour un compte issu de la
migration).

### En production

Pose `JOBHUNT_SECRET_KEY`, `JOBHUNT_ALLOWED_HOSTS` et
`JOBHUNT_CSRF_TRUSTED_ORIGINS`, et sers l'application en HTTPS (les cookies
sont marqués *Secure* par défaut en mode comptes). Les documents ne sont
jamais servis depuis `media/` par leur chemin : chaque téléchargement passe
par une vue qui vérifie le propriétaire — ne pas exposer `media/` avec le
serveur web. Il n'y a pas de réinitialisation de mot de passe par e-mail :
`changepassword` en tient lieu.

## Base de données

SQLite en local, PostgreSQL en production (Azure Database for PostgreSQL) :
le moteur est une configuration, pas un choix dans le code. Une seule variable
le fixe, `JOBHUNT_DATABASE_URL`, lue par `jobhunt/database.py` ; sans elle,
c'est `db.sqlite3` dans le dossier du projet, en WAL, comme avant.

| URL | Ce que ça ouvre |
| --- | --- |
| `sqlite:///db.sqlite3` | Un fichier **relatif au dossier du projet**, jamais au dossier courant — un service lancé depuis `/` ouvrirait sinon une base vide sans s'en apercevoir. |
| `sqlite:////var/lib/jobhunt/db.sqlite3` | Un chemin absolu (quatre barres). |
| `sqlite:///:memory:` | Une base en mémoire, perdue à l'arrêt. |
| `postgres://utilisateur:motdepasse@monserveur.postgres.database.azure.com:5432/jobhunt?sslmode=require` | PostgreSQL (`postgresql://` marche aussi). |

Dans le mot de passe, `@ : / # %` s'écrivent `%40 %3A %2F %23 %25`. Les
paramètres de la requête passent tels quels à libpq (`sslmode`, `sslrootcert`,
`connect_timeout`, `options`, `target_session_attrs`…) ; un hôte distant sans
`sslmode` reçoit `sslmode=require` d'office, Azure n'acceptant que TLS.

Sur PostgreSQL, une connexion est gardée ouverte entre les requêtes
(`JOBHUNT_DB_CONN_MAX_AGE`, 60 s par défaut ; `0` pour une connexion par
requête, `none` pour ne jamais la fermer). `?pool=1` ouvre à la place un pool
de connexions côté application — il demande `psycopg[pool]`, donc
`psycopg[pool]`, et met `CONN_MAX_AGE` à 0, Django refusant les deux à la
fois. Compte les connexions : un plan Azure de base en accorde peu, et chaque
worker de ton serveur d'application en garde une (ou un pool).

Le pilote s'installe de deux façons. En développement, sans passer par
`uv sync` — qui retirerait une extension installée à la main, voir
[Extensions](#extensions) :

```bash
uv pip install "psycopg[binary,pool]>=3.2"
```

Pour un déploiement, `uv sync --extra postgres` — et chaque `uv sync` suivant
doit garder `--extra postgres`, sinon psycopg disparaît du `.venv`. Un chemin
SQLite, lui, est pris tel quel (pas d'encodage pour-cent).

`migrate` reste une étape explicite du déploiement : un `runserver` local
pointé sur le serveur Azure n'applique rien de lui-même
(`JOBHUNT_AUTO_MIGRATE=1` pour le forcer). Une instance en mode comptes sur
SQLite déclenche l'avertissement `tracker.W001` au démarrage : ça marche, mais
une base partagée mérite PostgreSQL. Dernière différence visible : l'ordre
alphabétique des noms (sociétés, plateformes) suit la collation de la base
sur PostgreSQL, là où SQLite compare les octets.

Pour faire tourner la suite de tests contre un PostgreSQL local — le compte
doit pouvoir créer la base de test (`CREATEDB`, ce que donne l'image
officielle) :

```bash
docker run -d --name jobhunt-pg -e POSTGRES_USER=jobhunt -e POSTGRES_PASSWORD=jobhunt \
  -e POSTGRES_DB=jobhunt -p 127.0.0.1:55432:5432 postgres:16-alpine
JOBHUNT_DATABASE_URL=postgres://jobhunt:jobhunt@127.0.0.1:55432/jobhunt JOBHUNT_DEBUG=0 \
  uv run --no-sync manage.py test accounts tracker jobhunt
```

## Tests

```bash
uv run manage.py test accounts tracker jobhunt
uv run pyflakes jobhunt accounts tracker
```

Rendu de chaque page (y compris base vide), transitions de statut, tous les
points d'entrée HTMX, filtres, formulaires, l'import réel du classeur, les deux
modes d'entrée, l'isolation entre profils sur chaque URL, la migration de
rattachement rejouée sur une base d'avant les comptes, et (`jobhunt`) la
lecture de `JOBHUNT_DATABASE_URL`. Avec `JOBHUNT_DEBUG=0`, la suite tourne en
mode comptes sur SQLite : les avertissements `accounts.W001` et `tracker.W001`
s'affichent au démarrage, c'est attendu.

## Dépendances

| Fichier | Rôle |
| --- | --- |
| `pyproject.toml` | Les dépendances déclarées : Django et openpyxl, plus pyflakes et django-stubs dans le groupe `dev`, et `psycopg` dans l'extra `postgres` (un déploiement PostgreSQL fait `uv sync --extra postgres`, à chaque `sync`). |
| `uv.lock` | Les versions exactes, **à committer** : c'est ce qui rend l'environnement reproductible. |
| `.python-version` | Python 3.12 ; `uv` le télécharge tout seul s'il manque. |

```bash
uv add <paquet>              # ajouter une dépendance (met à jour pyproject + lock)
uv add --dev <paquet>        # idem, côté outillage
uv remove <paquet>           # retirer
uv lock --upgrade            # remonter les versions dans les bornes déclarées
uv sync                      # aligner .venv sur le lock
```

`uv run <commande>` fait le `sync` au besoin avant d'exécuter, donc l'environnement
ne peut pas se désynchroniser sans qu'on s'en aperçoive. Si un jour tu as besoin
d'un `requirements.txt` (déploiement qui ne connaît que pip) :

```bash
uv export --no-dev --format requirements-txt > requirements.txt
uv export --no-dev --extra postgres --format requirements-txt > requirements.txt   # avec psycopg
```

## Notes techniques

- Aucune dépendance chargée depuis un CDN : HTMX et les trois polices (Archivo,
  Newsreader, IBM Plex Mono) sont dans `static/`. L'application fonctionne hors ligne.
- Thème clair/sombre : suit le système, avec bascule manuelle mémorisée.
- Chaque page est rendue entièrement côté serveur ; HTMX ne sert qu'à éviter les
  rechargements. Sans JavaScript, la navigation, les filtres et les formulaires de
  création/modification fonctionnent ; les actions rapides (changer un statut,
  ajouter un événement, la fenêtre d'ajout) en ont besoin.

## Extensions

L'application découvre des extensions installées comme paquets Python via le
point d'entrée `jobhunt.plugins` (voir `jobhunt/plugins.py`) : app Django,
URLs montées sous leur préfixe, entrées de navigation, badges (la fonction
reçoit la requête et compte pour le profil connecté) et panneaux injectés dans
la fiche candidature. Le cœur n'en liste aucune en dur — installer le paquet
active l'extension, le désinstaller retire tout. Une extension range ses
propres données par profil comme le cœur : un champ `owner`, et
`accounts.services.owned_or_404` pour retrouver une ligne.

C'est le mécanisme qu'utilise le copilote IA (extension propriétaire,
développée hors de ce dépôt) :

```bash
uv pip install -e ../JobHunt-AI
uv run --no-sync manage.py runserver   # applique les migrations de l'extension
```

`uv sync` réaligne strictement `.venv` sur `uv.lock` et retire donc les
extensions installées à la main — d'où le `--no-sync` ci-dessus (déjà en
place dans `.claude/launch.json`) ; réinstalle l'extension après un `sync`.
Hors `runserver` (production, `JOBHUNT_AUTO_MIGRATE=0`), lance
`manage.py migrate` après l'installation.

## Architecture : ports et adaptateurs

Le cœur du suivi est découpé en hexagone : les règles et les cas d'usage ne
connaissent que des *ports* (des interfaces), et la base de données n'est qu'un
*adaptateur* branché dessus.

| Couche | Fichier | Rôle |
| --- | --- | --- |
| Règles | `tracker/domain.py` | Les règles pures — transition de statut, candidature en souffrance, relance due — comme des fonctions sur des instances, sans la moindre requête. |
| Ports | `tracker/ports.py` | Ce que les cas d'usage demandent à la persistance : `Persistence`, un dépôt par entité, `NotFound`. Il n'y passe jamais un queryset, seulement des instances, des listes, des entiers. |
| Adaptateurs | `tracker/adapters/django_orm.py` | L'ORM Django — SQLite comme PostgreSQL. |
| | `tracker/adapters/memory.py` | Des dictionnaires : pour tester sans base, et pour prouver que la frontière tient. |
| Cas d'usage | `tracker/services.py` | Changer un statut, noter une relance, rattacher un document… Chaque fonction reçoit la persistance et les seuils dont elle a besoin. |
| Adaptateur web | `tracker/views.py`, `tracker/queries.py` | Les vues traduisent la requête HTTP et rendent ; `queries.py` porte les lectures de pages (filtres, tableau de bord, analyse) directement en ORM. |

Les modèles restent les entités — les gabarits reçoivent toujours des instances
— et `tracker.models` reste l'API publique des extensions : rien ne change pour
elles.

### Ce que la couture achète, et ce qu'elle n'achète pas

- Une frontière explicite : `views.py` et `services.py` ne touchent jamais
  `Model.objects` — un test d'architecture le vérifie à chaque exécution.
- Des tests de règles sans base : les cas d'usage tournent sur
  `MemoryPersistence` dans un `SimpleTestCase`, en quelques millisecondes.
- Un point d'appui pour les extensions : appeler `services.change_status(...)`
  ou `persistence().applications.get(user, pk)` plutôt que refaire les règles.

En revanche, le moteur de base de données ne se choisit **pas** ici. SQLite et
PostgreSQL passent tous les deux par l'adaptateur ORM, et c'est
`JOBHUNT_DATABASE_URL` qui tranche (voir « Base de données »). Le réglage
`PERSISTENCE_ADAPTER` de `jobhunt/settings.py` désigne l'adaptateur des ports :
une couture de test et d'extension, pas un interrupteur de production.

### L'adaptateur mémoire et ses limites

`MemoryPersistence` range tout dans des dictionnaires — une instance, un
magasin. Il attribue les clés, les slugs et les horodatages, copie le
propriétaire sur un document rattaché et met `None` en fin de tri comme l'ORM.
Mais les instances qu'il contient restent des modèles Django : tout ce qui passe
par leurs gestionnaires inversés (`documents`, `contacts`, `events`),
`primary_cv`, les propriétés `is_stale` / `needs_attention`,
`owner_preferences`, `save`, `delete`, `log`, `apply_status` ou `Company.save`
atteint la base configurée — et lève `DatabaseOperationForbidden` dans un
`SimpleTestCase`. C'est voulu : passe par les ports et par `tracker.domain`.

### Écrire un test de règle sans base

```python
import datetime as dt

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase

from tracker import services
from tracker.adapters.memory import MemoryPersistence
from tracker.models import Application, Company, Status


class RelanceTests(SimpleTestCase):
    def test_l_envoi_programme_la_relance(self):
        store = MemoryPersistence()
        user = get_user_model()(pk=1, username="lionel")
        company = Company(pk=1, owner=user, name="Acme")
        application = store.applications.add(
            Application(owner=user, company=company, title="Poste", status=Status.TO_APPLY)
        )
        services.change_status(
            application, Status.SENT,
            follow_up_days=10, persistence=store, today=dt.date(2026, 9, 2),
        )
        self.assertEqual(application.follow_up_on, dt.date(2026, 9, 12))
```

Les seuils (`follow_up_days`, `stale_days`) sont toujours fournis par
l'appelant : ni les adaptateurs ni les cas d'usage ne lisent les préférences du
profil — c'est la vue qui les prend dans `request.preferences`.

### Utiliser les ports depuis une extension

```python
from django.http import Http404

from tracker import services
from tracker.adapters import persistence
from tracker.models import Status
from tracker.ports import NotFound

store = persistence()
try:
    application = store.applications.get(request.user, pk)
except NotFound:
    raise Http404
services.change_status(
    application, Status.SENT, follow_up_days=request.preferences.follow_up_days
)
```

L'API historique des modèles (`Application.objects.for_user`,
`application.apply_status`, `application.log`, `tracker.forms.company_for`,
`accounts.services.owned_or_404`) reste en place : les ports sont une option,
pas une obligation.

### La règle du NULL dans les tris

SQLite trie `NULL` comme la plus petite valeur, PostgreSQL comme la plus grande :
un `order_by("-score")` mettrait les offres non notées en tête sur PostgreSQL et
en queue sur SQLite. Tout tri sur une colonne qui accepte `NULL` (`score`,
`distance_km`, `applied_on`, `follow_up_on`, `closed_on`) s'écrit donc
`F("score").desc(nulls_last=True)` — Django émet `NULLS LAST` sur les deux
moteurs — et l'adaptateur mémoire fait pareil. `Meta.ordering` n'est jamais
utilisé implicitement : chaque lecture dit son `order_by`. L'ordre alphabétique
des noms, lui, suit la collation du moteur ; les tests s'en tiennent à
« croissant par nom ».
