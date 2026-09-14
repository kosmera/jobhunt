# Connexion par e-mail, bienvenue et contacts Brevo

## Connexion et confirmation d'adresse

En mode `JOBHUNT_AUTH_MODE=accounts`, la production utilise uniquement des
liens de connexion par e-mail. Ce fonctionnement est imposé lorsque
`JOBHUNT_DEBUG=0` ou `IS_SAAS_PRODUCTION=true` ; le réglage de développement
`JOBHUNT_PASSWORDLESS_AUTH=0` ne réactive pas les mots de passe en production.
Le mode local conserve son fonctionnement sur une machine de confiance.
Le mode comptes en développement conserve les formulaires à mot de passe tant
que l'essai explicite du nouveau parcours n'est pas activé.

L'inscription collecte le nom et l'adresse e-mail, puis demande de confirmer
cette adresse. Le compte n'est créé qu'après confirmation. Les réponses déjà
fournies pendant l'onboarding sont conservées avec la demande et reprises après
confirmation, y compris si le lien est ouvert sur un autre appareil. La simple
ouverture du lien affiche l'adresse concernée et un bouton de confirmation :
elle ne connecte pas le compte et ne consomme pas le lien. La confirmation
explicite par formulaire POST protégé contre le CSRF effectue l'action.

Le même mécanisme sert à la connexion et aux changements d'adresse depuis les
réglages. Lors d'un changement, l'adresse actuelle reste celle du compte jusqu'à
la confirmation du nouvel e-mail. La création de compte et la connexion ne
demandent aucun mot de passe, et les réglages de production n'en proposent pas.

Chaque lien est personnel, utilisable une seule fois, et expire 15 minutes
après la demande. Une adresse inconnue, inactive, ambiguë ou limitée reçoit la
même réponse générique dans l'interface, sans révéler l'existence d'un compte.
Le bouton « Renvoyer le lien » répète par formulaire POST la demande conservée
en session, après le délai autorisé. Une inscription conserve ainsi son nom,
son adresse et ses réponses. Sur l'écran d'un lien expiré, « Reprendre mon
inscription » permet de revenir au parcours.
Les demandes sont limitées à une par minute et cinq par heure pour une adresse,
ainsi qu'à vingt par heure pour une adresse IP. Les compteurs sont partagés
entre les processus web.
Sur Azure App Service, lorsque le proxy de confiance est activé et que
`WEBSITE_INSTANCE_ID` est présent, la limite utilise l'en-tête `Client-IP`
validé que le frontal Azure réécrit. Ailleurs, elle utilise l'adresse de la
connexion ; les en-têtes `X-Forwarded-For` fournis par le client sont ignorés.
Voir la [garantie du frontal Azure](https://github.com/Azure/app-service-linux-docs/blob/master/Things_You_Should_Know/headers.md).

Les e-mails texte et HTML utilisent le relais SMTP déjà configuré. Le serveur
web place uniquement l'UUID de la demande dans la file Django-Q2 ; le worker
commun `qcluster` construit et signe le lien au moment de l'envoi. Le lien
porteur d'accès n'est stocké ni dans la base ni dans la file. Le worker doit
être actif et traiter la demande avant son expiration. Aucun nouveau service
cloud, compte Brevo ou Redis n'est nécessaire.

La livraison fait au maximum trois essais, avec une reprise après 60 secondes
puis 120 secondes en cas d'échec. La commande `reconcile_launch_emails`, déjà
exécutée régulièrement en production, remet en file les essais arrivés à
échéance et récupère les envois interrompus tant que le lien reste valable.

Ces e-mails permettent de vérifier l'identité et de donner accès au compte.
Ils sont indépendants de l'accord facultatif pour le lancement commercial :
la confirmation d'une adresse n'inscrit pas automatiquement son propriétaire
à la liste Brevo et n'active ni abonnement ni droit Premium.

### Essayer le parcours en développement

Conserver les identifiants SMTP locaux et ajouter à `.env` :

```dotenv
JOBHUNT_DEBUG=1
JOBHUNT_AUTH_MODE=accounts
JOBHUNT_PASSWORDLESS_AUTH=1
JOBHUNT_PUBLIC_URL=http://localhost:8000
```

Appliquer les migrations, puis démarrer le serveur et le worker dans deux
terminaux avec les mêmes réglages :

```bash
uv run --env-file .env manage.py migrate
uv run --env-file .env manage.py runserver
```

```bash
uv run --env-file .env manage.py qcluster
```

Utiliser une adresse de test que l'on contrôle. Une demande depuis le navigateur
avec un relais SMTP configuré envoie réellement un e-mail. La commande
`test_launch_emails` décrite plus bas concerne uniquement la bienvenue et le
contact commercial ; elle ne teste pas les liens de connexion. Les tests
automatisés de `accounts` utilisent une boîte de sortie de test et n'envoient
aucun e-mail réel.

### Préparer la bascule en production

La migration `accounts.0009` ajoute les preuves de vérification et les demandes
de lien. Lorsqu'elle est exécutée en configuration de production, elle remplace
les anciens mots de passe par une valeur inutilisable : les anciens hachages
ne restent pas dans la base active. Cette suppression n'est pas réversible et
ne modifie pas les sauvegardes antérieures. Les sessions historiques sont
invalidées par le nouveau moteur d'authentification ; les utilisateurs devront
confirmer leur adresse par un lien pour se reconnecter.

Avant cette bascule, chaque compte à conserver doit avoir une adresse réelle,
accessible et unique, y compris les comptes d'administration. Une adresse
absente ou partagée entre plusieurs comptes ne permet pas d'identifier le
compte de manière sûre. Vérifier ces données et la livraison SMTP avant
d'appliquer la migration ; les comptes administrateurs suivent aussi la
connexion par e-mail et conservent leurs droits existants.

La configuration versionnée dans JobHunt-Infra possède déjà
`JOBHUNT_AUTH_MODE=accounts`, `JOBHUNT_DEBUG=0`, les identifiants SMTP et
`JOBHUNT_PUBLIC_URL=https://tonjobideal.com`. Le démarrage applique les migrations
avec le rôle de maintenance et lance le worker commun même sans copilote.
Aucun nouveau drapeau de production ni nouveau processus n'est nécessaire.
Le serveur et le worker doivent recevoir la même URL publique, la même clé
secrète et les mêmes réglages SMTP ; les redémarrer avec la même version.

Les URL des liens de confirmation contiennent une preuve d'accès temporaire.
Le démarrage de JobHunt-Infra doit être déployé avec le format de journal
Gunicorn qui ne conserve que la méthode HTTP, le statut, le volume de réponse
et la durée. Les chemins, paramètres et en-têtes de provenance n'y figurent
pas. Les erreurs Django masquent également le jeton des liens dans leurs
messages et traces. Les éventuels journaux d'accès du proxy doivent suivre la même règle.
Cette modification opérationnelle est versionnée dans le dépôt privé
JobHunt-Infra et doit accompagner la version applicative.

## Bienvenue et contacts Brevo

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
