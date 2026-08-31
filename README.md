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

Puis <http://localhost:8000>. C'est un outil local mono-utilisateur : pas de
compte à créer, pas d'écran de connexion.

Pour l'administration Django (`/admin`, édition brute des tables), il faut un
compte :

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

### Automatismes

Le suivi tient les dates tout seul, pour que tu n'aies qu'un geste à faire :

- passer une candidature en « envoyée » date l'envoi du jour et programme une
  relance à +10 jours ;
- « Relance faite » consigne la relance dans le fil et repousse la suivante ;
- clôturer une candidature date la clôture et retire la relance ;
- au-delà de 14 jours sans nouvelles, une candidature envoyée remonte dans
  « À traiter maintenant ».

Ces deux délais se règlent par variable d'environnement, voir `.env.example`.

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
`--skip-files` (données seules, sans copier les documents).

Le dossier personnel — `00_Suivi_candidatures.xlsx`, `00_LISEZMOI.md`, `Offres/`
et `CV_base/` — est **hors du dépôt**, avec `db.sqlite3` et `media/`. C'est
délibéré : le code est publiable, les CV et les analyses ne le sont pas. Sur un
clone neuf il n'y a donc rien à importer ; l'application démarre sur une base
vide, et chaque page a son état vide.

## Base de données

SQLite (`db.sqlite3`), en WAL. Le passage à PostgreSQL ne demande que de changer
`DATABASES` dans `jobhunt/settings.py` puis de relancer `migrate` — aucun code
applicatif n'en dépend.

## Tests

```bash
uv run manage.py test tracker
uv run pyflakes jobhunt tracker
```

63 tests : rendu de chaque page (y compris base vide), transitions de statut,
tous les points d'entrée HTMX, filtres, formulaires, et l'import réel du classeur.

## Dépendances

| Fichier | Rôle |
| --- | --- |
| `pyproject.toml` | Les dépendances déclarées : Django et openpyxl, plus pyflakes dans le groupe `dev`. |
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
```

## Notes techniques

- Aucune dépendance chargée depuis un CDN : HTMX et les trois polices (Archivo,
  Newsreader, IBM Plex Mono) sont dans `static/`. L'application fonctionne hors ligne.
- Thème clair/sombre : suit le système, avec bascule manuelle mémorisée.
- Chaque page est rendue entièrement côté serveur ; HTMX ne sert qu'à éviter les
  rechargements. Sans JavaScript, la navigation, les filtres et les formulaires de
  création/modification fonctionnent ; les actions rapides (changer un statut,
  ajouter un événement, la fenêtre d'ajout) en ont besoin.
