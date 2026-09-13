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

Puis <http://localhost:8000>. Sans session, la première visite ouvre la page
de présentation. « Commencer » lance le parcours de création du profil,
sans mot de passe en local. Voir [Comptes et profil](#comptes-et-profil).

Ni `makemigrations` ni `migrate` à lancer à la main : en développement,
`runserver` écrit la migration d'un modèle modifié, puis applique tout ce qui
est en attente avant de servir — et de nouveau à chaque rechargement, donc
aussi après un `git pull`. Une seule
réserve : quand Django devrait te poser une question (un renommage possible,
un champ obligatoire sans valeur par défaut — la mauvaise réponse perd des
données), rien n'est généré et le démarrage te renvoie à
`manage.py makemigrations`. `JOBHUNT_AUTO_MIGRATE=0` rend à `runserver` son
comportement d'origine ; en production, `migrate` reste une étape explicite du
déploiement. L'automatisme ne s'applique qu'à une base locale : un
`runserver` de développement pointé sur un serveur distant (voir
[Base de données](#base-de-données)) ne génère ni n'applique rien de
lui-même — `JOBHUNT_AUTO_MIGRATE=1` pour le forcer en connaissance de cause.

En mode `local`, le premier profil est automatiquement administrateur de
l'installation : ouvre **Données brutes** dans la barre latérale ou `/admin/`,
sans créer un second compte ni saisir de mot de passe. Une session existante
(comme le profil historique `local`) est mise à niveau à la prochaine requête.
Les profils supplémentaires restent des utilisateurs ordinaires. Ce rôle
n'ouvre pas le [copilote IA](#copilote-ia), réservé aux comptes Premium ; la
date Premium est en lecture seule dans l'administration locale.

Pour une instance auto-hébergée avec connexion par mot de passe
(`JOBHUNT_AUTH_MODE=accounts`), crée explicitement l'administrateur :

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
| **Copilote** | Le copilote IA (`/copilote/`), pour les comptes Premium : analyse du CV en profil candidat, évaluation d'une offre, CV ciblé, veille sur les sites d'offres. Voir [Copilote IA](#copilote-ia). |
| **Réglages** | Ton profil (nom, titre, point de départ, e-mail), ce que tu cherches (les réponses du parcours Bienvenue : postes, secteurs, expérience, formation, contrats, mode de travail, villes, salaire, horizon, situation), tes préférences (délais de relance et de signalement, rayon, langue de CV), ton mot de passe, et la suppression du compte. Accessible depuis ton nom, en bas de la barre latérale. |

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
d'entrer, choisies par `JOBHUNT_AUTH_MODE`. Dans les deux modes, un visiteur
non connecté sur `/` est dirigé vers la page de présentation `/accueil/` ;
un utilisateur connecté y retrouve son tableau de bord. Les règles suivantes
s'appliquent ensuite à l'entrée dans l'espace privé :

| Mode | Quand | Comment on entre |
| --- | --- | --- |
| `local` (défaut si `JOBHUNT_DEBUG=1`) | ta machine | Aucun mot de passe. Pas de profil → parcours **Bienvenue** (une vingtaine d'écrans : situation, postes visés, secteurs, mode de travail, salaire, CV… ; le nom est demandé juste avant le CV, puis le tableau de bord). Un profil → connecté d'office. Plusieurs → on choisit dans une liste, et « Changer de profil » en bas de la barre latérale y ramène. |
| `accounts` (défaut si `JOBHUNT_DEBUG=0`) | une instance partagée | **Connexion** (e-mail ou identifiant + mot de passe) et **Inscription**, qui crée le compte et enchaîne sur le parcours Bienvenue. Un visiteur anonyme peut aussi commencer par le parcours : le compte se crée juste avant le CV. `JOBHUNT_SIGNUP_OPEN=0` ferme les inscriptions et renvoie un visiteur anonyme vers la connexion ; un compte créé autrement (`createsuperuser`) passe par le parcours à sa première visite. |

### Le parcours Bienvenue

Un écran par question, dans l'ordre : situation, outils d'IA déjà essayés,
principale difficulté, attentes, postes visés, secteurs, expérience, formation,
type de contrat, mode de travail, villes et rayon (sauf en télétravail), salaire
minimum, horizon, puis un bilan à relire. Le nom (ou le compte, en mode
`accounts`) est demandé ensuite, juste avant le CV : c'est le premier moment où
une ligne doit exister en base. Les réponses restent en session jusqu'au dernier
écran — un parcours interrompu reprend au même endroit sur le même navigateur,
et repart du début ailleurs, le nom déjà connu.

Ce que chaque réponse alimente :

- **Profil** : le premier poste visé devient le titre, la première ville le
  point de départ (s'ils étaient vides) ;
- **Préférences** : le rayon de recherche, la langue du CV, et une relance
  proposée à 7 jours au lieu de 10 pour qui a besoin d'un poste rapidement ;
- **Profil de recherche** : postes, secteurs, expérience, formation, contrats,
  mode de travail, villes, salaire, horizon, et les réponses de contexte —
  tout se modifie ensuite dans **Réglages**, section « Ce que tu cherches » ;
- **Documents** : le CV téléversé devient le CV de base de la bibliothèque. Le
  fichier ne quitte jamais l'espace du profil ; seule une version anonymisée du
  texte est transmise au copilote quand il est activé, et l'écran le dit tel
  quel. L'analyse du CV est gratuite, y compris pendant le parcours : l'écran
  suit l'attente, l'extraction et l'enregistrement, puis affiche le résumé,
  les compétences, les langues avec leurs niveaux, les expériences avec leurs
  dates et le nombre de formations extraites. Les informations manquantes sont
  signalées et un rappel invite à vérifier le résultat avec le CV d'origine.
  Le pourcentage mesure uniquement l'envoi du fichier ; les étapes de
  l'analyse reflètent l'état réel du worker.

Les états du parcours sont une machine à états finis (`accounts/onboarding/`) :
un tableau d'étapes avec des gardes, vérifié à l'import et par les tests ; le
bouton Retour, la modification depuis le bilan et le compteur en découlent.

### Reprise d'une base existante

La migration qui a introduit les comptes rattache tout ce qui existait déjà à
un profil : le seul compte présent s'il y en a un, sinon un compte `local`
sans mot de passe créé pour l'occasion. En mode local, le premier compte
reçoit aussi l'accès administrateur. À la visite suivante, le parcours
Bienvenue s'ouvre sur un écran « reprise » qui annonce combien de
candidatures attendent, puis complète ce compte — rien n'est à ressaisir.

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
Sur PostgreSQL, le serveur se connecte avec le rôle applicatif et les
migrations avec le propriétaire : voir [Isolation des données](#isolation-des-données-rls).

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

Le pilote s'installe de deux façons. En développement, sans toucher au reste
du `.venv` :

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
  uv run manage.py test accounts tracker jobhunt rls jobhunt_ai
```

## Stockage des fichiers

Les CV et les annonces téléversés sont des données sensibles ; où ils vivent
est une configuration, comme le moteur de base : `JOBHUNT_STORAGE_PROVIDER`.

| Fournisseur | Où vont les octets | Comment on les récupère |
| --- | --- | --- |
| `local` (défaut) | `media/` dans le dossier du projet — un dossier privé, **jamais** servi par son chemin | Une vue de l'application (`/documents/<id>/telecharger/`) qui vérifie le propriétaire et diffuse le fichier ; un lien signé (`/fichiers/<jeton>/`) pour tout ce qui a besoin d'une adresse |
| `azure` | Un conteneur Azure Blob Storage (`JOBHUNT_AZURE_STORAGE_CONTAINER`, `documents` par défaut) | La même vue, qui redirige vers une URL SAS en lecture seule, valable `JOBHUNT_STORAGE_LINK_TTL` secondes (300) |
| `memory` | La mémoire du processus — perdus à l'arrêt (tests, démonstrations) | Comme `local` |

Trois façons de s'authentifier auprès d'Azure, de la plus recommandée à la
plus pratique : l'URL du compte seule (`JOBHUNT_AZURE_STORAGE_ACCOUNT_URL`),
et c'est l'identité managée du service qui signe — il lui faut les rôles
*Storage Blob Data Contributor* et *Storage Blob Delegator* sur le compte
(et `AZURE_CLIENT_ID` pour une identité affectée par l'utilisateur) ;
l'URL du compte plus une clé (`JOBHUNT_AZURE_STORAGE_ACCOUNT_KEY`, via une
référence Key Vault sur App Service) ; ou la chaîne de connexion
(`JOBHUNT_AZURE_STORAGE_CONNECTION_STRING`, pratique en développement et avec
Azurite). Le pilote s'installe avec `uv sync --extra azure` (ou, sans toucher
au `.venv`, `uv pip install azure-storage-blob azure-identity`).

```bash
uv run manage.py storage_status            # fournisseur, conteneur, signature, lien d'essai
uv run manage.py storage_status --probe    # écrit puis supprime un fichier (droits d'écriture)
uv run manage.py storage_prune             # liste les fichiers qu'aucun document ne référence
uv run manage.py storage_prune --delete    # …et les supprime
```

Les octets sont écrits avant la ligne, pour qu'une ligne ne désigne jamais
un fichier absent. Le prix, c'est l'autre sens : une requête qui échoue
après l'écriture laisse le fichier. `storage_prune` ramasse ce que plus
aucune ligne ne désigne, en épargnant les dépôts de moins de 24 heures
(`--older-than`) — un téléversement en cours a ses octets écrits et sa
ligne pas encore. Il lit les documents de tous les profils : sur
PostgreSQL, il se lance avec le rôle propriétaire, comme `dumpdata` et les
sauvegardes.

Pour exercer le vrai SDK sans compte Azure, l'émulateur officiel suffit ; la
suite ajoute alors cinq tests d'intégration (sinon ils sont sautés) :

```bash
docker run -d --name jobhunt-azurite -p 127.0.0.1:10000:10000 \
  mcr.microsoft.com/azure-storage/azurite azurite-blob --blobHost 0.0.0.0
JOBHUNT_STORAGE_PROVIDER=azure JOBHUNT_AZURE_STORAGE_CONNECTION_STRING=UseDevelopmentStorage=true \
  uv run manage.py storage_status --probe
uv run manage.py test tracker    # AzuriteTests s'exécutent
```

`manage.py check` reste hors ligne : il signale un pilote manquant
(`tracker.E002`, `tracker.E003`) ou le stockage en mémoire (`tracker.W002`),
sans toucher au réseau. C'est `storage_status` qui parle au fournisseur.

### Un port, trois adaptateurs

Le cœur ne connaît qu'un port, `StoragePort` (`tracker/ports.py`) :
`save_file`, `open_file`, `delete_file`, `file_exists`, `get_secure_url` et
`extract_and_anonymize_text`. Chaque adaptateur est aussi le *storage* Django
que `STORAGES["default"]` désigne : `Document.file`, la suppression en
cascade, `import_legacy` et le copilote passent par le même objet sans le
savoir, et aucune migration n'a été nécessaire — sauf la colonne
`size_bytes`, qui évite un aller-retour réseau par ligne affichée quand les
fichiers sont chez Azure.

Un lien de `get_secure_url` vaut cinq minutes (`JOBHUNT_STORAGE_LINK_TTL`,
une heure au plus — une URL SAS ne se révoque pas avant son expiration). Sur
Azure c'est un vrai *jeton au porteur* : quiconque la détient lit ce blob
jusqu'à `se`. En local, le lien est signé, lié au compte inscrit dans le
chemin du fichier, **et vérifié contre les lignes** : la vue redemande à la
persistance si le compte connecté possède bien un document portant ce
fichier. Un lien forgé, emprunté ou périmé se lit donc comme un mauvais
identifiant — 404. `document.file.url` renvoie ce lien, jamais un chemin
`media/`.

Un lien de téléchargement se pose en `<a href>` ordinaire, jamais en
`hx-get` ni sous `hx-boost` : sur Azure la vue répond une redirection vers
`blob.core.windows.net`, qu'un `fetch` HTMX ne peut pas suivre (CORS).

Un incident de stockage ne casse pas une page : chaque adaptateur traduit
les erreurs de son fournisseur en `StorageError` (une `OSError`), la taille
des documents est gardée sur la ligne (`size_bytes`) plutôt que redemandée à
chaque affichage, et le client Azure est réglé pour abandonner en quelques
secondes — les valeurs d'origine du SDK font attendre une minute.

### Un CV vers l'IA, anonymisé

Le cas d'usage `services.ingest_cv` fait tout passer par le port : il
enregistre les octets, relit le texte (PDF, DOCX, TXT, MD) et l'anonymise
(`tracker/privacy.py` : e-mails, téléphones, liens, IBAN, numéro national,
date et lieu de naissance, âge, adresse, et ce que le profil sait de son
propriétaire — nom, identifiant, e-mail, point de départ, téléphone —
remplacés par `[EMAIL]`, `[TELEPHONE]`, `[NOM]`…), puis remet ce texte, et
seulement lui,
à la couche IA. Celle-ci est un second port, `CVAnalyzer` : le cœur publie le
texte sur le signal `tracker.events.cv_ingested`, auquel le copilote abonne
`jobhunt_ai.signals.receive_cv` au démarrage (`tracker.adapters.cv_analyzer()`
rend alors un `CVEventPublisher`) ; `settings.CV_ANALYZER` sert aux tests, et
sans abonné — `COPILOT_ENABLED=0` — le CV est simplement rangé. L'analyseur
est appelé dans la requête : il doit rendre la main tout de suite et lancer
son travail depuis `rls.on_commit`. Un CV illisible — format inconnu, scan sans couche texte,
moins de 200 caractères extraits — est conservé et non analysé : le cœur n'a
aucun repli qui enverrait le fichier lui-même.

Ce qui traverse le port est anonymisé, le libellé compris : il reprend par
défaut le nom du fichier téléversé, qui porte le nom du candidat bien plus
souvent que le corps du CV. Le libellé rangé en base, lui, reste tel quel —
c'est celui que son propriétaire relit dans sa bibliothèque. Et si
l'analyse échoue, quelle qu'en soit la raison, le document reste rangé :
un téléversement ne se perd pas parce que la couche IA a trébuché.

L'anonymisation est une heuristique, pas un modèle : elle masque ce qu'elle
reconnaît, et le reste du CV arrive intact. Elle ne cherche ni la
nationalité, ni la situation familiale, ni le permis de conduire ; une année
seule en début de ligne suivie d'une ville (`2018 Liège, Belgique`) est
ambiguë et masquée par prudence, tout comme un code postal qui suit
immédiatement une rue. Rien de ce qu'elle masque n'est récupérable côté IA :
ce que le copilote doit réafficher — nom, e-mail, point de départ,
téléphone — vient du profil du compte, pas du CV. Le téléphone y est
facultatif : laissé vide, il ne figure nulle part. Les liens, eux, ne sont
inscrits nulle part ailleurs que dans le CV : ils sont donc perdus pour l'IA.

## Isolation des données (RLS)

Chaque page ne montre que les lignes du profil connecté : c'est la première
couche, celle du code (`Model.objects.for_user(user)`, les ports qui prennent
un `owner`). Sur PostgreSQL, une seconde couche fait respecter la même
frontière par la base elle-même, avec la *row-level security* native : chaque
table qui porte les données d'un compte (candidatures, sociétés, documents,
profil, préférences, événements, contacts, la table des comptes) reçoit une
politique `tenant_isolation`, et le serveur web se connecte avec un rôle
soumis à ces politiques. Une requête que le code aurait oublié de filtrer ne
ramène rien ; une écriture pour un autre compte est refusée
(`ProgrammingError`, « new row violates row-level security policy »). Sur
SQLite, rien de tout cela n'existe : la première couche reste seule, comme
avant.

### Deux rôles

| Rôle | Fait quoi | Connexion |
| --- | --- | --- |
| propriétaire (sur Azure : l'administrateur du serveur) | `manage.py migrate`, `rls_status`, `rls_grant`, les commandes d'import | `JOBHUNT_DATABASE_URL` au moment de migrer |
| `jobhunt_app` (`JOBHUNT_DB_APP_ROLE`) | sert l'application | `JOBHUNT_DATABASE_URL` du serveur web |

La migration `rls.0001` crée le rôle applicatif s'il manque (sans mot de
passe, `NOLOGIN`), lui donne `SELECT/INSERT/UPDATE/DELETE` sur toutes les
tables, pose les *default privileges* pour celles que les migrations
suivantes créeront, et pose les politiques. Reste à l'ops, une fois :

```bash
# avec les identifiants du propriétaire
psql "$OWNER_URL" -c "ALTER ROLE jobhunt_app LOGIN PASSWORD '…';"   # ou une identité Entra ID
uv run manage.py rls_status --probe                                  # tout doit être « ok »
```

Le serveur web reçoit alors `JOBHUNT_DATABASE_URL=postgres://jobhunt_app:…@…/jobhunt?sslmode=require`.
Un propriétaire ignore par nature les politiques de ses tables (pas de `FORCE
ROW LEVEL SECURITY`, exprès : les migrations de données doivent tout voir) ;
c'est pourquoi le serveur ne doit jamais tourner avec lui. La première
requête PostgreSQL d'un processus le vérifie : rôle ni superutilisateur, ni
`BYPASSRLS`, ni propriétaire d'une table protégée, et politique en place sur
chaque table enregistrée. Avec `JOBHUNT_RLS_ENFORCE=1` (défaut en mode
comptes hors `DEBUG`) la requête échoue sinon ; sans, un avertissement est
journalisé une fois. `manage.py check --database default` fait le même examen
(`rls.E004` avec le rôle applicatif, `rls.W002` avec le propriétaire) — y
compris toute table lisible par le rôle applicatif qui n'a pas de politique,
celles du copilote comprises —, et
`manage.py rls_status` l'affiche table par table (`--probe` : passe au rôle
applicatif et compte ce qu'une session sans compte voit — zéro partout, sauf
`auth_user`).

Si une migration est un jour appliquée par un autre rôle que celui qui a posé
les *default privileges*, les tables neuves ne sont pas données au rôle
applicatif : `rls_status` le signale, `manage.py rls_grant` (avec le
propriétaire) répare. Le `SET ROLE jobhunt_app` que `--probe` et la suite de
tests utilisent n'est accordé au propriétaire que s'il administre ce rôle
(il l'a créé, ou il est superutilisateur) ; sinon la migration l'indique par
un avertissement et tout le reste vaut quand même.

### Comment la base sait pour qui elle travaille

Chaque transaction annonce le compte au serveur —
`set_config('app.current_user_id', '<id>', true)` — jamais la session : le
réglage meurt au `COMMIT`/`ROLLBACK`, donc une connexion réutilisée par la
requête suivante (connexions persistantes, pool psycopg, PgBouncer d'Azure en
mode transaction) ne peut pas garder le compte précédent. Trois endroits
l'annoncent :

- le middleware `rls.middleware.RowLevelSecurityMiddleware`, juste après
  l'authentification : toute la requête (les middlewares suivants, la vue,
  le rendu) tourne dans une transaction liée au compte connecté, ou à
  personne pour un visiteur anonyme ; une réponse 5xx annule la transaction ;
  une connexion en cours de requête (`auth.login`, l'entrée automatique du
  mode local) relie le reste de la requête au nouveau compte ;
- le moteur `rls.backends.postgresql` (celui que `JOBHUNT_DATABASE_URL`
  configure), pour toute transaction ouverte ailleurs pendant qu'un compte
  est lié ;
- `rls.as_user(user)` pour tout ce qui n'est pas une requête : commandes
  (`import_legacy` l'utilise), tâches de fond (le worker du copilote), tests.
  `as_user(None)` pose une question « pour personne » : c'est ainsi que la
  table des comptes, seule à rester lisible sans compte lié (il faut bien
  trouver le compte pour le connecter), répond à l'unicité d'un e-mail ou
  au compteur de profils du mode local.

Une requête SQL hors transaction n'est liée à personne et ne voit rien ; une
`IntegrityError`/`DatabaseError` attrapée sans `atomic()` imbriqué laisse la
transaction de la requête en erreur. Les deux se voient tout de suite en
développement sur PostgreSQL. Trois conséquences pour tout ce qui n'est pas
une requête :

- **un thread ne hérite pas du contexte** (Python démarre un thread avec un
  contexte vide) : une tâche de fond se lance depuis `rls.on_commit(...)` —
  la ligne qu'elle doit lire n'est visible qu'une fois la requête validée —
  et entre elle-même dans `with rls.as_user(owner_id):`, une transaction par
  phase plutôt qu'une seule autour d'un long appel réseau ; un
  `transaction.on_commit` brut tourne sans compte ;
- **une commande lancée avec les identifiants du serveur web ne voit rien**
  hors `as_user` : `dumpdata` produit un fichier vide et valide, `shell`
  compte zéro ligne. Sauvegardes (`pg_dump`), restaurations, `loaddata`,
  `flush` se font avec le propriétaire ; après une restauration,
  `rls_grant` puis `rls_status --probe` avant de relancer le serveur (un
  `pg_restore --no-owner` fait par le rôle applicatif le rendrait
  propriétaire, donc exempt des politiques) ;
- **l'admin Django devient mono-compte** : connecté par le rôle applicatif,
  un superutilisateur n'y voit que ses propres lignes et ne peut en créer
  pour personne d'autre. L'assistance inter-comptes se fait dans
  `manage.py shell` avec le propriétaire, à l'intérieur de
  `rls.as_user(user)`.

### Un propriétaire de groupe

Le propriétaire des tables est le rôle qui a lancé la première migration.
Sur Azure, l'administrateur du serveur, une identité Entra ID `isAdmin` ou
une identité d'intégration continue sont des rôles distincts : le second à
migrer se heurte à « must be owner of table ». Le remède est un rôle de
groupe sans mot de passe qui possède tout, dont chaque administrateur est
membre, et que les migrations endossent :

```bash
psql "$ADMIN_URL" -c "CREATE ROLE jobhunt_owner NOLOGIN;" -c "GRANT jobhunt_owner TO <admin>;"
psql "$ADMIN_URL" -c "REASSIGN OWNED BY <admin> TO jobhunt_owner;"        # base existante
JOBHUNT_DATABASE_URL="postgres://<admin>:…@…/jobhunt?sslmode=require&assume_role=jobhunt_owner" \
  uv run manage.py migrate && uv run manage.py rls_grant
```

`assume_role` est l'option de Django qui fait `SET ROLE` à la connexion (sur
le port 5432, jamais à travers PgBouncer) : tout objet créé appartient au
groupe et les *default privileges* valent quel que soit l'administrateur.
`rls_status` signale des tables à propriétaires différents. Même schéma pour
une identité Entra ID côté serveur web : `jobhunt_app` reste un rôle de
privilèges sans mot de passe, l'identité (créée `isAdmin=false`) le reçoit
par `GRANT jobhunt_app TO "<identité>"` et se connecte elle-même — un jeton
Entra expire, donc pas dans `JOBHUNT_DATABASE_URL` en dur : un fichier
d'environnement rafraîchi par un sidecar, et `JOBHUNT_DB_CONN_MAX_AGE`
court.

### Sur Azure, en production

- **PgBouncer** (port 6432, mode transaction) convient : le compte est
  annoncé par transaction, jamais par session. Mais chaque requête tient
  une connexion serveur du début à la fin : dimensionne
  `default_pool_size` ≥ workers × instances, et ne fais jamais de session
  psql ad hoc avec les identifiants du rôle applicatif à travers PgBouncer
  (un `SET` de session y contaminerait la connexion suivante ; le serveur
  annonce « personne » à chaque transaction anonyme justement pour ça, et
  refuse de démarrer si la connexion arrive avec une valeur déjà posée).
- **Délais** : `idle_in_transaction_session_timeout` et `statement_timeout`
  posés sur `jobhunt_app` (`ALTER ROLE jobhunt_app SET …`), plus longs que la
  requête légitime la plus lente ; pas de `JOBHUNT_AI_EAGER=1` en
  production (un appel LLM tiendrait la transaction ouverte).
- **Migrations** : `ENABLE ROW LEVEL SECURITY` et `CREATE POLICY` prennent
  un verrou exclusif sur la table et attendent les requêtes en cours ;
  lance-les hors pointe avec `?options=-c%20lock_timeout%3D5s` et une
  reprise en cas d'échec.
- **Recherche** : `icontains`/`istartswith` ne sont pas *leakproof*, un
  index texte sur une table protégée serait ignoré ; l'égalité, `IN` et les
  intervalles de dates gardent leurs index.

### Ce que ça change dans le code

- Les *slugs* sont uniques par compte, plus globalement : le rôle applicatif
  ne voit que ses lignes, une vérification globale serait mensongère. Le
  chemin des fichiers porte l'identifiant du compte.
- Un modèle qui référence le compte s'enregistre dans le `ready()` de son
  app (`rls.register("app.Model", owner="user")`, ou `via="application"`
  pour un satellite) et pose sa politique dans une migration
  (`rls.operations.EnableRowLevelSecurity`). Le contrôle `rls.W001` signale
  tout modèle oublié — ceux du copilote compris : `jobhunt_ai/apps.py`
  enregistre ses sept tables et déclare exemptes celles de `django_q`, une
  file partagée que le worker lit sans compte lié.
- Jamais de fonction `SECURITY DEFINER` ni de vue sans `security_invoker`
  sur une table protégée : elles contournent les politiques.
- Une connexion (`auth.login`) se fait au niveau de la requête, jamais à
  l'intérieur d'un `as_user` imbriqué : `rebind` le refuse, sinon le compte
  serait perdu à la sortie du bloc.
- La table des comptes : lisible et modifiable par une session sans compte
  (connexion, inscription, mise à niveau d'un ancien hachage), mais jamais
  supprimable ainsi ; une session liée n'y voit que sa ligne.
- Conséquence de la ligne précédente : se connecter à un *autre* compte sans
  s'être déconnecté (page de connexion de l'admin) échoue, la session liée ne
  voyant pas la ligne visée. Les vues de l'application redirigent avant.
- Les politiques portent sur le propriétaire de la ligne, pas sur ses clés
  étrangères : un bug de la première couche pourrait rattacher un document du
  compte A à une candidature du compte B (B ne le verrait pas pour autant) ;
  le modèle le refuse (`Document.save`, `Application.save`), la base ne le
  vérifie pas.

### Dans la suite de tests

Sur PostgreSQL, chaque requête du client de test est faite avec le rôle
applicatif (`rls.testing`, installé par `TEST_RUNNER`) : les tests de pages
existants exercent les politiques sans rien changer, les fixtures restent
posées par le superutilisateur. Les tests propres aux politiques
(`rls/tests.py`) sont sautés sur SQLite.

## Tests

Le [pipeline GitHub de validation](docs/ci.md) lance Ruff, Pyright et les tests
sur SQLite et PostgreSQL, copilote compris, puis une fois de plus le cœur seul
avec `COPILOT_ENABLED=0`. Les [hooks Git](docs/ci.md#install-git-hooks)
bloquent les commits invalides et lancent les tests SQLite avant chaque push.
Le guide indique la commande d'installation à exécuter une fois par clone.

```bash
uv run manage.py test accounts tracker jobhunt rls jobhunt_ai
uv run pyflakes jobhunt accounts tracker rls jobhunt_ai
pyright   # installé à part (uv tool install pyright) ; lit [tool.pyright] de pyproject.toml
```

Les tests du copilote tournent avec un modèle simulé (`jobhunt_ai/tests/fakes.py`) :
aucun appel réseau, aucun coût. Ceux de la production SaaS importent le SDK
`openai`, donc la suite complète veut l'extra `azure` (`uv sync --all-extras`,
ce que fait le pipeline).

Sous Claude Code, `.claude/settings.json` branche un hook (`.claude/hooks/check-python.sh`)
qui relance `pyright` et `pyflakes` sur tout le projet après chaque fichier Python
modifié par l'assistant : une erreur lui revient dans la foulée au lieu d'attendre
la prochaine relecture.

Rendu de chaque page (y compris base vide), transitions de statut, tous les
points d'entrée HTMX, filtres, formulaires, l'import réel du classeur, les deux
modes d'entrée, l'isolation entre profils sur chaque URL, la migration de
rattachement rejouée sur une base d'avant les comptes, et (`jobhunt`) la
lecture de `JOBHUNT_DATABASE_URL`. Avec `JOBHUNT_DEBUG=0`, la suite tourne en
mode comptes sur SQLite : les avertissements `accounts.W001`, `accounts.W002`
et `tracker.W001` s'affichent au démarrage, c'est attendu.

## Dépendances

| Fichier | Rôle |
| --- | --- |
| `pyproject.toml` | Les dépendances déclarées : Django, openpyxl, pypdf et python-docx (texte des CV) ; pour le copilote, django-q2 (file durable), anthropic, openai, langgraph, pydantic, httpx, beautifulsoup4 et langchain-mcp-adapters (Bright Data) ; plus pyflakes, django-stubs et requests (tests d'intégration Azurite) dans le groupe `dev`, `psycopg` dans l'extra `postgres`, `azure-storage-blob` + `azure-identity` dans l'extra `azure`, gunicorn + whitenoise dans l'extra `deploy` (un déploiement fait `uv sync --extra postgres --extra azure --extra deploy`, à chaque `sync`). |
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

## Copilote IA

Le copilote est l'app `jobhunt_ai`, livrée avec le cœur et activée par défaut.
`COPILOT_ENABLED=0` l'éteint d'un bloc : son app et `django_q` sortent de
`INSTALLED_APPS`, ses URLs (`/copilote/`), son entrée de navigation, son panneau
sur la fiche candidature et l'analyse de CV disparaissent avec elles, et rien de
`jobhunt_ai` n'est importé. Dans le code, la vérité du moment est
`apps.is_installed("jobhunt_ai")` ; les gabarits reçoivent `copilot_enabled`
(`tracker.context_processors.navigation`), et `tracker/context_processors.py`
n'importe `jobhunt_ai.hooks` que si l'app est là. Sur une base PostgreSQL déjà
migrée avec le copilote, ses tables `django_q_*` restent en place : elles ne
portent que des identifiants d'exécution et `rls` les autorise par nom
(`rls.sql.QUEUE_TABLES`), donc l'instance reste valide sans rien supprimer.

Quatre agents, construits avec **LangGraph** (orchestration) et les SDK officiels
**OpenAI / Anthropic** (appels au modèle, sorties structurées) :

| Agent | Ce qu'il fait |
| --- | --- |
| **Analyse de CV** | PDF, DOCX, TXT ou MD → profil candidat structuré (compétences, expériences, formation…). Le texte est extrait et **anonymisé par le cœur** avant d'arriver ici (voir [Un CV vers l'IA, anonymisé](#un-cv-vers-lia-anonymisé)) ; un scan sans couche texte est rangé et laissé de côté. |
| **Évaluation** | profil × offre → score de compatibilité (écrit dans `Application.score`), verdict, forces/faiblesses/stratégie, et lacunes synchronisées vers la page Analyse. |
| **Génération de CV** | profil + offre + dernière évaluation → CV ATS ciblé (DOCX sobre : une colonne, pas de tableaux), rangé dans les documents de la candidature. |
| **Veille** | requêtes dérivées du profil → lecture des sites d'offres configurés (via **Bright Data**, qui déjoue les protections anti-robot) → extraction par le modèle → dédoublonnage contre tes candidatures → pistes **pré-triées** à trier, importables en un clic. |

Le pré-tri de la veille et l'évaluation partagent **une seule définition du
score** (`SCORE_SCALE`, dans `jobhunt_ai/agents/qualifications.py`), incluse
mot pour mot dans les deux invites, avec les seuils des pastilles de
l'interface : 80 et plus « candidature évidente », 65 à 79 « ça vaut le
coup », 50 à 64 « pari risqué ». Le pré-tri ne voit qu'un extrait de la page
de résultats et le dit par sa **fiabilité** plutôt qu'en maquillant la note ;
l'évaluation lit l'annonce entière. La grille de qualifications qu'utilise la
veille est dérivée une fois par profil (`CandidateProfile.qualifications`) ;
un nouveau CV donne un nouveau profil, donc une grille fraîche.

Ce que le copilote écrit dans le cœur passe par `jobhunt_ai/services/` :
`applications.py` (import d'une piste, rapport d'évaluation → `Application`,
lacunes → `SkillGap`, CV généré → `Document`), `documents.py` (réception d'un
CV, écriture d'un document hors transaction) et `accounts.py` (identité et
préférences du compte). Ses sept tables portent une politique RLS
(`jobhunt_ai/migrations/0006_rls.py`, `0009`, `0010` ; règles déclarées dans
`jobhunt_ai/apps.py`), et le worker ouvre un `rls.as_user(owner_id)` autour de
chaque accès à la base, jamais autour d'un appel au modèle ou du scraping.

### Lancer le copilote

Le serveur web met les agents en file ; un processus **Django-Q2** à part les
exécute. Sans lui, les tâches restent en attente. Ces commandes chargent `.env`
(à créer d'après `.env.example`), que Django ne lit pas de lui-même :

```bash
uv run --env-file .env manage.py runserver
# Dans un deuxième terminal :
uv run --env-file .env manage.py qcluster
```

Après une modification du code des agents ou de `.env`, arrête puis relance
le worker : `qcluster` ne recharge pas ces changements automatiquement.
Garde un seul cluster local actif. Pour utiliser une clé OpenAI, définis aussi
`JOBHUNT_AI_PROVIDER=openai` ; sans fournisseur explicite, une instance locale
utilise Anthropic. Relance également le serveur web après un changement de `.env`.

Hors `runserver` (production, `JOBHUNT_AUTO_MIGRATE=0`), `manage.py migrate`
reste une étape explicite : les migrations de `jobhunt_ai` et de `django_q`
s'appliquent avec les autres. Sur PostgreSQL, web et worker tournent avec le
rôle applicatif, les migrations avec le propriétaire (voir
[Isolation des données](#isolation-des-données-rls)). En production, supervise
le worker et lance `manage.py reconcile_ai_runs` chaque minute pour clôturer
les tâches expirées (`--owner-id` pour un seul compte) ; `deploy/` contient
les unités systemd du worker et du minuteur. Le guide
[docs/async-tasks.md](docs/async-tasks.md) décrit la file, le contrat de l'API
JSON (`/copilote/api/…`), les délais et la reprise après interruption.

### Accès Premium

En mode local auto-hébergé (`JOBHUNT_AUTH_MODE=local`, `IS_SAAS_PRODUCTION=false`),
les comptes actifs ont **Premium par défaut**, y compris les profils existants
sans date d'expiration. Ce défaut ne dépend ni de `DEBUG`, ni du statut
d'administrateur, et ne crée pas de période payée artificielle en base.

Dans **Administration → Profils**, le **Niveau d'abonnement** et **Premium
jusqu'au** sont modifiables en mode local comme en mode `accounts` :

- **Automatique** (défaut) : une date future accorde Premium ; sans date,
  Premium est inclus en mode local hors SaaS et le compte est gratuit ailleurs.
- **Gratuit** : désactive Premium, même si une date future est renseignée.
- **Premium** : accorde Premium jusqu'à la date renseignée, ou sans limite
  de durée si elle est vide.

Toute date atteinte ou dépassée met fin à Premium, y compris en local. Les
périodes payées existantes conservent leur date. La colonne **Premium actif**
indique l'accès effectif. Les paramètres du compte accessibles à l'utilisateur
ne permettent pas de modifier ces droits.

`accounts.services.has_premium(user)` relit le niveau, la date et l'état actif
du compte à chaque contrôle, à la mise en file et avant chaque étape d'une tâche ;
`profile.is_premium` en est le miroir sur une instance déjà chargée.
Aucune clé de licence ni clé API n'est demandée aux utilisateurs.
Les pages Premium du copilote répondent 402 à un compte gratuit, un fragment
HTMX le renvoie vers `/copilote/`. L'analyse de CV et le suivi de cette analyse
sont accessibles aux comptes gratuits actifs, depuis les documents comme
pendant le parcours Bienvenue. Les autres opérations gardent leur contrôle
d'accès ; les quotas de l'instance commerciale restent applicables.

En attendant Stripe, les abonnements sont gérés dans cette administration ;
aucun checkout ni webhook de paiement n'est encore implémenté.

Une instance commerciale (`IS_SAAS_PRODUCTION=true`) ouvre le copilote à tout
compte et le mesure par quotas de requêtes (gratuit/payant), les appels
utilisant le fournisseur choisi par `JOBHUNT_AI_PROVIDER` : voir
[docs/azure-saas.md](docs/azure-saas.md). Le déploiement de validation utilise
OpenAI directement et conserve `IS_SAAS_PRODUCTION=false`.

La clé du fournisseur IA appartient à l'exploitant. Son absence ne bloque ni
Django ni l'accès Premium : un appel IA échoue alors proprement avec un
message d'indisponibilité, le détail restant dans les journaux.

### Configuration

Le copilote lit des réglages Django `JOBHUNT_AI_*` (`jobhunt_ai/settings.py`
tient les défauts) ; `jobhunt/settings.py` les remplit depuis les variables
d'environnement du même nom, et accepte encore les anciens noms Anthropic et
Bright Data. `.env.example` en donne le résumé.

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `COPILOT_ENABLED` | `1` | `0` pour une instance sans copilote |
| `JOBHUNT_AI_PROVIDER` | compatibilité historique | `openai`, `azure_openai` ou `anthropic` ; indépendant des droits utilisateurs |
| `JOBHUNT_AI_OPENAI_API_KEY` | — | clé OpenAI Platform, fournie par Key Vault sur App Service ; `OPENAI_API_KEY` aussi accepté localement |
| `JOBHUNT_AI_API_KEY` | — | secret Anthropic, géré par l'exploitant ; facultatif au démarrage, utilisé seulement à l'exécution (`ANTHROPIC_API_KEY` reste accepté) |
| `JOBHUNT_AI_MODEL` | `claude-opus-5` | modèle utilisé |
| `JOBHUNT_AI_MAX_TOKENS` | `16000` | jetons de sortie par appel |
| `JOBHUNT_AI_LOCATION` / `_RADIUS_KM` | `Nivelles, Belgique` / `40` | replis de la veille quand le profil n'a ni point de départ ni rayon |
| `JOBHUNT_AI_SCOUT_SOURCES` | ICTjob, Jobat, LinkedIn, Indeed | sources de la veille, en JSON, voir ci-dessous |
| `JOBHUNT_AI_SCOUT_MAX_QUERIES` / `_MAX_PAGES` / `_PAGE_CHARS` | 2 / 8 / 28000 | garde-fous de la veille (le plafond de pages doit couvrir requêtes × sources) |
| `JOBHUNT_AI_EAGER` | `0` | réservé aux doublures de tests ; refusé par les checks de démarrage |
| `JOBHUNT_Q_WORKERS` / `_TIMEOUT` / `_RETRY` | 2 / 1800 / 3 × timeout + 120 | processus du cluster, durée maximale d'une tâche et délai de remise en file (secondes) |
| `JOBHUNT_AI_QUEUE_TTL` | `86400` | attente maximale en file avant expiration (secondes) |
| `JOBHUNT_AI_WORKER_GRACE` | `60` | marge ajoutée au délai d'une tâche réclamée (secondes) |
| `JOBHUNT_AI_SCRAPE_TIMEOUT` / `_ANALYZE_TIMEOUT` | 180 / 900 | délais par cible de veille, lecture puis analyse (secondes, au plus le timeout du cluster) |
| `JOBHUNT_AI_LLM_TIMEOUT` / `_MAX_RETRIES` | 120 / 2 | délai réseau par appel au modèle et reprises du SDK |
| `JOBHUNT_AI_BRIGHTDATA_API_TOKEN` | — | jeton [Bright Data](https://brightdata.com/cp/mcp) (`BRIGHTDATA_API_TOKEN` aussi accepté) |
| `JOBHUNT_AI_BRIGHTDATA_MCP_URL` | `https://mcp.brightdata.com/mcp` | point d'accès MCP ; le jeton y est ajouté à la volée |
| `JOBHUNT_AI_BRIGHTDATA_TIMEOUT` | `90` | secondes ; débloquer une page peut être long |
| `JOBHUNT_AI_BRIGHTDATA_FALLBACK` | `1` | `0` pour voir l'erreur Bright Data au lieu de retomber sur la lecture directe |

Les réglages de l'offre hébergée (`IS_SAAS_PRODUCTION`, `JOBHUNT_AI_OPENAI_*`, `JOBHUNT_AI_AZURE_*`,
`JOBHUNT_AI_FREE_*` / `_PAID_*`, `JOBHUNT_AI_UPGRADE_URL`) sont décrits dans
[docs/azure-saas.md](docs/azure-saas.md).

### Bright Data

`httpx` + BeautifulSoup se font refuser par la plupart des sites d'offres
(anti-robot, rendu JavaScript, CAPTCHA). La veille passe donc par le **serveur
MCP distant de Bright Data** (`jobhunt_ai/scraping/brightdata.py`), qui
débloque la page et la rend en Markdown — déjà propre pour le modèle. Sans
jeton, on retombe sur la lecture directe, qui ne marche plus que sur les
sites les plus ouverts. Récupère le jeton sur <https://brightdata.com/cp/mcp>
et pose-le dans `.env` :

```bash
BRIGHTDATA_API_TOKEN=…
```

Transport *streamable HTTP*, authentification par `?token=` : rien à
installer, pas de Node.js. Les deux outils utilisés — `scrape_as_markdown` et
`search_engine` — font partie du socle toujours exposé. Pour en ouvrir
d'autres, ajoute les paramètres de Bright Data à l'URL :

```bash
JOBHUNT_AI_BRIGHTDATA_MCP_URL="https://mcp.brightdata.com/mcp?groups=browser"
```

`brightdata.py` expose aussi `get_tools()`, qui rend **tous** les outils du
serveur sous forme d'outils LangChain — de quoi confier le choix à un agent
(`create_react_agent(model, brightdata.get_tools())`) au lieu de les appeler
soi-même. La veille, elle, les appelle directement : le traitement reste
déterministe, testable et borné en jetons.

Le jeton doit être vu par le **worker** : posé dans le seul processus web, il
ne configure rien. Si Jobat ou Indeed répond 403, vérifie-le sans rien
afficher de secret :

```bash
uv run --env-file .env python -c 'from jobhunt_ai.scraping.brightdata import is_configured; print(is_configured())'
```

Un worker déjà lancé garde ses réglages en instantané : redémarre-le après
avoir changé un jeton ou une source.

### Sources de la veille

Par défaut : **ICTjob**, **Jobat**, **LinkedIn** et **Indeed**. Les deux
derniers ne sont lisibles qu'à travers Bright Data — c'est précisément là que
le blocage anti-robot était total.

Deux formes de source, avec les emplacements `{query}`, `{location}` et
`{radius}` :

```json
[
  {"name": "ICTjob", "url": "https://www.ictjob.be/fr/chercher-emplois-it?keywords={query}"},
  {"name": "LinkedIn", "url": "https://www.linkedin.com/jobs/search?keywords={query}&location={location}&f_TPR=r604800"},
  {"name": "Indeed", "url": "https://be.indeed.com/emplois?q={query}&l={location}&radius={radius}&fromage=7"},
  {"name": "Google", "search": "{query} emploi Brabant wallon", "engine": "google", "geo_location": "be"}
]
```

- `url` — une page de résultats à lire ;
- `search` — une recherche passée au moteur (`google`, `bing` ou `yandex`),
  qui ne dépend d'aucun site en particulier.

`{location}` et `{radius}` reçoivent le point de départ et le rayon du profil
(à défaut `JOBHUNT_AI_LOCATION` / `_RADIUS_KM`) ; un gabarit qui ne s'en sert
pas — les sites belges ne prennent que les mots-clés — reste valide. Dans une
source `url` ils sont encodés pour la chaîne de requête, dans une source
`search` ils restent en clair.

Deux détails qui ont demandé une vérification sur pièce :

- **Indeed** : le chemin français `/emplois`, pas `/jobs` — ce dernier renvoie
  une page vide même à travers Bright Data. Ses liens d'annonce sont relatifs,
  donc résolus contre l'URL de recherche.
- **LinkedIn** : la page publique liste une soixantaine d'offres avec des
  liens absolus, sans connexion. `f_TPR=r604800` et `fromage=7` limitent les
  deux sources aux sept derniers jours, ce qu'attend une veille.

L'extraction des annonces est faite par le modèle, donc pas de sélecteurs CSS
à maintenir : un changement de mise en page ne casse pas une source.

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
| | `tracker/adapters/file_storage.py`, `tracker/adapters/azure_storage.py` | Les fichiers : le disque (`media/`), la mémoire, Azure Blob Storage — voir [Stockage des fichiers](#stockage-des-fichiers). |
| Confidentialité | `tracker/privacy.py` | L'anonymisation du texte d'un CV avant qu'il ne parte vers l'IA : règles pures sur des chaînes. |
| Cas d'usage | `tracker/services.py` | Changer un statut, noter une relance, rattacher un document, ingérer un CV… Chaque fonction reçoit la persistance, le stockage et les seuils dont elle a besoin. |
| Adaptateur web | `tracker/views.py`, `tracker/queries.py` | Les vues traduisent la requête HTTP et rendent ; `queries.py` porte les lectures de pages (filtres, tableau de bord, analyse) directement en ORM. |

Les modèles restent les entités — les gabarits reçoivent toujours des instances
— et `tracker.models` reste l'API publique du copilote et de toute autre app :
rien ne change pour eux.

### Ce que la couture achète, et ce qu'elle n'achète pas

- Une frontière explicite : `views.py` et `services.py` ne touchent jamais
  `Model.objects` — un test d'architecture le vérifie à chaque exécution.
- Des tests de règles sans base : les cas d'usage tournent sur
  `MemoryPersistence` dans un `SimpleTestCase`, en quelques millisecondes.
- Un point d'appui pour le copilote et toute autre app : appeler `services.change_status(...)`
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

Un cas d'usage qui touche à un fichier prend le stockage de la même façon —
`storage=MemoryStorageAdapter()` — et reçoit le contenu explicitement,
`upload=SimpleUploadedFile(...)`. Sans ça, le test écrirait dans le `media/`
configuré :

```python
from django.core.files.uploadedfile import SimpleUploadedFile

from tracker.adapters.file_storage import MemoryStorageAdapter

store, files = MemoryPersistence(), MemoryStorageAdapter()
intake = services.ingest_cv(
    user, None, Document(kind=DocumentKind.CV), upload=SimpleUploadedFile("cv.txt", texte),
    label="Mon CV", known=privacy.known_identity(display_name="Lionel Dupont"),
    analyzer=None, persistence=store, storage=files,
)
```

La suite complète tourne de toute façon avec un `MEDIA_ROOT` temporaire
(`rls.testing.TestRunner`) : un test distrait ne salit pas le dossier du
projet.

### Utiliser les ports depuis une autre app

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
