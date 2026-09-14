# E-mails de bienvenue et contacts Brevo

La validation de l'écran de collecte crée deux `LaunchEmailJob` dans la même
transaction que l'accord et la fin du parcours. Chaque traitement a un message
Django-Q2 ORM signé, contenant uniquement son identifiant et celui du compte.
Le serveur web n'appelle ni SMTP ni Brevo. Passer cette étape sans accord ne
crée aucun traitement.

Le worker commun `qcluster` exécute indépendamment :

- `welcome` : confirmation en français, texte et HTML, avec la formule choisie,
  le lien vers l'espace gratuit et le tarif Premium prévu de 24,90 € par mois.
- `contact_sync` : création du contact Brevo ou ajout à la liste de lancement
  configurée. Les désinscriptions globales et celles de cette liste sont
  respectées, sans réactiver un contact bloqué. L'adresse seule est transmise ;
  la formule et la preuve de consentement restent dans JobHunt, sans inventer
  des attributs Brevo qui n'auraient pas été provisionnés.

Les tâches ne modifient ni l'adresse de connexion ni les droits Premium. Une
clé API manquante empêche la synchronisation mais pas l'e-mail de bienvenue.
L'acceptation SMTP confirme la remise au relais ; les rebonds et l'arrivée en
boîte de réception se vérifient ensuite dans les journaux transactionnels Brevo.

## Démarrage local

Conserver les réglages SMTP déjà testés dans `.env`, puis ajouter :

```dotenv
JOBHUNT_BREVO_API_KEY=ta-cle-api-brevo
# Facultatif : identifiant d'une liste existante, pour retrouver ces contacts.
JOBHUNT_BREVO_LAUNCH_LIST_ID=123
JOBHUNT_PUBLIC_URL=http://localhost:8000
```

Utiliser une **clé API Brevo**, distincte de `JOBHUNT_EMAIL_PASSWORD` (clé SMTP).
Retirer la ligne de liste si aucune liste n'est choisie. L'expéditeur doit rester
celui déjà validé dans Brevo. Voir les variables SMTP dans `.env.example`.
Ne pas committer les secrets.

Appliquer les migrations avant de démarrer les processus :

```bash
uv run --env-file .env manage.py migrate
```

### Tester sans parcourir l'inscription

Le mode local garde la collecte automatique désactivée. Pour vérifier le contenu
sans créer de compte, envoyer de message ni modifier Brevo :

```bash
uv run --env-file .env manage.py test_launch_emails --to toi@example.org
```

L'aperçu utilise les vrais gabarits de bienvenue. Ajouter `--plan free` pour
prévisualiser la formule gratuite ; la formule par défaut est Premium.

Pour exécuter réellement l'envoi SMTP et la synchronisation Brevo, sans serveur
web, parcours d'inscription ni `qcluster` :

```bash
uv run --env-file .env manage.py test_launch_emails --to toi@example.org --send
```

La commande crée un compte de test dédié à cette adresse, avec mot de passe
inutilisable, et exécute les deux traitements durables habituels. Elle ne modifie
aucun profil existant et ne traite aucun autre message de file. Un seul compte
est conservé par adresse : répéter la commande affiche les états existants et ne
renvoie pas une bienvenue réussie. Une autre formule pour ce même compte est
refusée pour préserver les données du premier test ; les aperçus restent libres.
Les messages de file terminés par ce test sont retirés, les états sont conservés.

Après correction d'un réglage manquant ou incorrect :

```bash
uv run --env-file .env manage.py test_launch_emails --to toi@example.org --send --retry-failed
```

Seuls les échecs sont repris ; un envoi `uncertain` reste à vérifier dans Brevo
avec la commande de résolution décrite plus bas. Un résultat inachevé produit
un code de sortie non nul. Le contact créé pendant un test réel reste dans Brevo.

Cette commande refuse la production, les hébergements Azure App Service et les
bases distantes. Elle exige `DEBUG` actif, `IS_SAAS_PRODUCTION=false`, et SQLite
ou PostgreSQL sur une adresse locale. Aucun drapeau ne contourne ces restrictions.

En production, le superviseur de JobHunt-Infra lance `qcluster` et la reprise
des traitements. L'option `--backfill` inclut les adresses déjà collectées avec accord, pour les comptes
actifs ayant terminé le parcours. Il crée uniquement les traitements manquants.
On peut omettre ce drapeau pour ne traiter que les nouvelles inscriptions, ou
ajouter `--owner-id 42` pour limiter le rattrapage à un compte de test. Ne pas
utiliser une copie de données réelles pour des essais de livraison.

L'écran de collecte demande `JOBHUNT_AUTH_MODE=accounts` et
`JOBHUNT_LAUNCH_INTEREST_ENABLED=1`, posé explicitement par Terraform en production.
Ce drapeau est indépendant de `IS_SAAS_PRODUCTION`. En l'absence de réglage,
il est désactivé avec `JOBHUNT_DEBUG=1` et actif avec `JOBHUNT_DEBUG=0`.
Le développement ne collecte pas d'accord
automatiquement ; utiliser `test_launch_emails` pour un essai explicite.
La file reste disponible lorsque
`COPILOT_ENABLED=0`. Django ne charge pas `.env` tout seul ; les processus web
et de traitement doivent recevoir les mêmes réglages. Relancer le worker après une modification
du code ou des identifiants. Aucun Redis ni nouveau service cloud n'est requis.

## Reprises et suivi

Un verrouillage conditionnel réserve chaque traitement avant le réseau. Une
contrainte unique `(user, kind)` et les états terminaux empêchent les messages
de file dupliqués de renvoyer un e-mail réussi. La synchronisation du contact
peut reprendre sans renvoyer la bienvenue. Les appels réseau ont lieu hors
transaction ; les lectures et écritures sont limitées au compte par `as_user`.
La migration `0008` installe aussi sa politique RLS sur PostgreSQL.

Les erreurs réseau Brevo, HTTP 429/5xx et refus SMTP temporaires sont réessayés
au maximum cinq fois, avec des délais de 1, 5, 25 puis 60 minutes. Le processus
de réconciliation récupère les essais arrivés à échéance et les messages de
file perdus. Une interruption pendant un envoi SMTP devient `uncertain` ; un
contact interrompu peut être resynchronisé. Une désactivation du compte ou un
accord changé avant l'exécution annule le traitement.

L'administration affiche les traitements en lecture seule, filtrables par
type et état. Comme les profils, elle reste soumise à l'isolation par compte.
Les codes d'erreur ne contiennent ni adresse, ni secret, ni réponse brute du
fournisseur ; la file partagée ne contient pas les destinataires.

Après correction des réglages d'un traitement `failed` (par exemple une clé
API absente ou un identifiant SMTP incorrect), relancer explicitement :

```bash
uv run --env-file .env manage.py reconcile_launch_emails --owner-id 42 --retry-failed
```

Un timeout après la transmission SMTP peut signifier que Brevo a accepté le
message sans que le worker ait reçu l'accusé. Aucun nouvel envoi automatique
n'est fait dans ce cas. Vérifier les journaux Brevo, à l'heure et pour le
destinataire du traitement. Son Message-ID est
`<launch-welcome-ID_DU_TRAITEMENT@tonjobideal.com>`.
Résoudre ensuite **un seul** résultat confirmé :

```bash
# Brevo a bien accepté le message :
uv run --env-file .env manage.py resolve_launch_email --owner-id 42 --job-id 7 --delivered
# OU : les journaux confirment qu'il n'a pas été envoyé ; autoriser un nouvel essai :
uv run --env-file .env manage.py resolve_launch_email --owner-id 42 --job-id 7 --not-delivered
```

## Production et validation

Le démarrage et les références de secrets sont versionnés dans **JobHunt-Infra**.
Le superviseur y démarre `qcluster` même sans copilote et exécute
`reconcile_launch_emails --backfill` toutes les 60 secondes. Les migrations
s'exécutent avec la connexion de maintenance ; le worker utilise le rôle
applicatif normal, sans `BYPASSRLS`. Le nom historique de cluster `jobhunt-ai`
reste inchangé pour conserver les messages existants. Une mise en production
nécessite le déploiement de ces modifications et la configuration de la clé API.

Les tests mockent SMTP et HTTP, sans envoyer de messages ni modifier Brevo :

```bash
uv run manage.py test accounts.test_brevo accounts.test_email_delivery accounts.test_email_commands
```

Ils couvrent la transaction d'inscription, le contenu signé de la file, les
reprises indépendantes, les désinscriptions, les limites par compte, les erreurs
SMTP ambiguës, les commandes de récupération et les gabarits réels.
