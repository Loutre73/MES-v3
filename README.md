# MES v3

MES standard pour automates Siemens S7-1500 en OPC UA : historian à 1 s sur changement, suivi de production, arrêts, alarmes, dérives moteurs, énergie et recettes. Le même produit se déploie sur chaque site ; tout ce qui est propre à un site est de la configuration (`deploy/site.env`) ou de la donnée (catalogue de tags).

Documents de conception : [comparaison des architectures](https://claude.ai/artifact/KLxPmR3DEiuxjA5J1ddfvc) · [architecture détaillée](https://claude.ai/artifact/KyNtLLU61mcpawd3Cti1yq)

## Contenu

Application web en français (une seule adresse, comptes Active Directory ou locaux) :

| Écran | Ce qu'on y fait |
| --- | --- |
| Synthèse usine | Indicateurs clés (TRS, production, débit, énergie, arrêts, alarmes) par poste, jour, semaine ou mois, puis tuiles agrandissables : production, TRS, arrêts à traiter (qualification en un clic), alarmes, cartes des lignes avec frise du poste, causes d'arrêt, carte des moteurs, énergie, poste et recettes |
| Détail d'une ligne | Frise du poste (marche, ralenti, arrêts) : on sélectionne un arrêt sur la frise pour le qualifier, le découper, puis la frise passe au suivant ; synoptique des moteurs de la ligne avec courant sur 24 h, production par heure vs objectif, recette planifiée comparée à la production, alarmes |
| Planning | La semaine sur une page : 24 h (6 h → 6 h) en abscisse, les jours en ordonnée, toutes les lignes empilées dans chaque jour ; chaque barre est un poste d'équipe. Glisser-déposer d'une recette, d'une maintenance, d'un arrêt planifié ou d'une équipe sur un poste, ou d'un poste sur un autre pour les échanger ; postes passés verrouillés |
| Arrêts | Arrêts détectés automatiquement (signal de marche, code d'arrêt automate), qualification par cause, découpe d'un arrêt en deux, Pareto |
| Postes et arrêts planifiés | Liste des postes générés automatiquement (3×8 par défaut), objectif, équipe, arrêts planifiés sur une plage horaire |
| Alarmes | Alarmes en cours et historique, acquittement, recherche |
| Moteurs | Vue des 100 moteurs colorée par dérive de courant, courbes, recalage de la référence après intervention |
| Énergie | Puissance appelée, consommation par compteur, kWh/t par poste et par ligne |
| Recettes | Création, duplication, versions avec validation, recherche (y compris par valeur de paramètre), comparaison avec la production en cours, chargement dans l'automate avec accusé de réception |
| Analyse | Tableaux Grafana intégrés (performance, énergie, tendances historian, santé de la collecte), sans second mot de passe |
| Catalogue | Découverte automatique des machines de l'automate, attributs, import/export CSV des machines et des tags |
| Paramètres, Utilisateurs | Seuils, rôles, comptes locaux, journal des actions |

Interface aux couleurs marine, bleu clair et vert, en thème clair ou sombre (bouton dans chaque bandeau, choix mémorisé par poste de travail). La synthèse, le détail de ligne et le planning tiennent dans la hauteur de l'écran (agrandir une tuile la superpose aux autres) ; la police Inter est livrée avec l'application, qui reste utilisable sans Internet.

Rôles : opérateur, chef d'équipe, maintenance, administrateur. Avec l'AD, le rôle vient des groupes (`AUTH_LDAP_GROUP_ROLES`).

| Service | Rôle |
| --- | --- |
| `web` | Point d'entrée unique (nginx) : application, API sous `/api`, Grafana sous `/grafana` |
| `api` | API FastAPI, moteur d'événements (arrêts, alarmes), calculs périodiques (postes, KPI, énergie, dérives) |
| `db` | PostgreSQL 16 + TimescaleDB : historian, catalogue, données MES |
| `migrate` | Applique les migrations SQL (`db/migrations`) et les durées de conservation, à chaque démarrage |
| `collector` | Client OPC UA : abonnements à 1 s, deadband par tag, écriture par lots, tampon disque, rechargement du catalogue à chaud |
| `grafana` | Tableaux de bord livrés en fichiers (`grafana/dashboards`) |
| `simulator` | Simulateur S7-1500 (profil `sim`) : 10 000 tags (moteurs, compteurs, lignes, mesures) et DB de recette ; règles et scénarios pilotés depuis la console d'exploitation |
| `ops` | Agent d'exploitation : sert la console `/systeme` (état des services, démarrage et arrêt, journaux, historisation, simulation) |
| `prometheus`, `node-exporter`, `docker-exporter`, `process-exporter`, `postgres-exporter` | Surveillance technique (profil `monitoring`), voir « Supervision technique » |

## Démarrage rapide (démonstration avec simulateur)

Prérequis : Linux, Docker et Docker Compose v2.

```bash
cd deploy
./mesctl install --demo     # construit les images, démarre la pile, le simulateur et la découverte
./mesctl status             # tags actifs, points/s, retard de collecte, taille de l'historian
```

L'application répond sur le port 80 (`WEB_PORT`). Compte de secours : `admin`, mot de passe `ADMIN_PASSWORD` dans `deploy/site.env` (affiché en fin d'installation).

## Installation sur un site

```bash
cd deploy
./mesctl install            # crée site.env avec des secrets générés
# renseigner OPCUA_ENDPOINT, la sécurité OPC UA, BROWSE_ROOT et AUTH_LDAP_* dans site.env
./mesctl install            # récupère les images de MES_VERSION et démarre
```

Ensuite, dans **Catalogue > Découverte** (ou `./mesctl discover`) : le MES parcourt l'automate sous `BROWSE_ROOT`, reconnaît les instances d'après les types de `catalog/types/*.yaml` (signature de membres), et crée machines, tags, unités, deadbands, rôles et alarmes. Les structures inconnues peuvent être importées en tags bruts. Les attributs propres au site (cadence nominale des lignes, ligne d'un compteur, courant nominal d'un moteur) se saisissent dans **Catalogue > Machines** ou par import CSV. La collecte prend tout changement en compte sans redémarrage.

Captures d'écran sur le simulateur : [docs/captures](docs/captures).

Recettes : le bloc à créer dans l'automate est décrit dans [docs/automate-recettes.md](docs/automate-recettes.md).

Pour un site sans Internet : `./mesctl package 0.1.0` sur un poste connecté, copier l'archive, puis `./mesctl update 0.1.0 --offline mes-images-0.1.0.tar`.

## Mises à jour

```bash
./mesctl update 0.2.0       # sauvegarde, nouvelles images, migrations, redémarrage, contrôle de santé
./mesctl rollback           # version précédente + restauration de la sauvegarde d'avant mise à jour
./mesctl backup             # sauvegarde complète (rotation selon BACKUP_KEEP)
```

Les nouveaux paramètres d'une version sont ajoutés automatiquement à `site.env`. Le collecteur redémarre en dernier. Pendant une coupure de la base, il garde les valeurs dans son tampon disque (`/spool`) et les réécrit dès que la base répond.

Les versions sont publiées par la CI : un tag Git `v0.2.0` construit et pousse `ghcr.io/loutre73/mes-*:0.2.0`.

## Historian

- `historian.value` : une ligne par changement (`time`, `tag_id`, `value`, `quality` 0 bon / 1 incertain / 2 mauvais). Les booléens valent 0 ou 1.
- Chunks de 6 h compressés après 12 h, conservation `HISTORIAN_RETENTION` (1 an par défaut).
- `historian.value_1min` : agrégats à la minute (moyenne, min, max, dernière valeur), compressés, conservation `AGGREGATE_1MIN_RETENTION` (1 an).
- `historian.value_1h` : agrégats horaires calculés depuis les agrégats minute, conservation `AGGREGATE_1H_RETENTION` (3 ans).
- Entre deux changements, la valeur est la dernière connue : `historian.value_at(tag_id, instant)`, ou `locf()` dans les requêtes Grafana. Le collecteur réécrit la dernière valeur des tags immobiles toutes les 15 min pour borner ces recherches.
- Une perte de connexion à l'automate écrit une valeur de qualité mauvaise pour chaque tag.

## Console d'exploitation

Page web séparée **http://serveur/systeme** (hors de l'application MES, connexion avec un compte MES ou AD). Lecture pour toute session, actions réservées au rôle **admin**, toutes tracées dans le journal des opérations.

| Onglet | Contenu |
| --- | --- |
| Architecture | Schéma vivant des services : état (vert, orange, rouge), débits sur les liaisons (valeurs OPC UA/s, points écrits/s, requêtes/s), hôte (CPU, mémoire, disque). Un clic sur un bloc : détail, actions, derniers journaux |
| Services et commandes | Démarrer, arrêter, redémarrer **l'application** (collecteur, API, Grafana ; la base, le web et la console restent en marche) ou un service ; historisation (interrupteur, suspension 15 min, 1 h ou jusqu'à réactivation) ; disque et volumes ; journaux de chaque service ; journal des opérations |
| Simulation | Règles du simulateur par DB, modifiées à chaud (arrêts par heure, durée des arrêts, dérive des moteurs, bruit, cadence...) ; historisation par DB ; valeurs des tags en direct avec courbe ; scénarios (panne de ligne, micro-arrêts, défaut ou dérive moteur, pic de consommation, coupure OPC UA) |
| Supervision Grafana | Le tableau Grafana « Système » ci-dessous (profil `monitoring`) |

- **Historisation suspendue** : le collecteur reste connecté à l'automate et suit les valeurs, mais n'écrit plus dans l'historian ; arrêts, alarmes et KPI ne sont plus alimentés pendant ce temps. À la reprise, la dernière valeur de chaque tag est réécrite. L'état survit à un redémarrage du collecteur.
- **Agent d'exploitation** (`ops/ops_agent.py`, bibliothèque standard Python) : indépendant de l'API, la console reste disponible application arrêtée. Il accède au socket Docker (équivalent root sur l'hôte) mais n'accepte qu'une liste fermée d'actions (démarrer, arrêter, redémarrer, journaux) sur les services du projet ; ni `web` ni la console ne peuvent être arrêtés depuis la console. Le collecteur (port 9109) et le simulateur (port 8090) exposent leur contrôle sur le réseau Docker seulement.
- **Accès sans identifiant** : `AUTH_DISABLED=true` dans `site.env` (puis `./mesctl start`) ouvre l'application, Grafana et la console sans connexion, chaque visiteur étant administrateur. Pour un poste de développement ou une démonstration seulement, de préférence avec `WEB_BIND=127.0.0.1` pour que l'application ne soit joignable que depuis ce poste.
- Sans session, la page affiche son propre formulaire de connexion (il nécessite l'API : si l'application est arrêtée et la session expirée, relancer avec `./mesctl start`).

## Supervision technique

Tableau Grafana **Système - supervision technique**, dans l'onglet « Supervision Grafana » de la console, pour le développement et l'exploitation.

| Section | Contenu |
| --- | --- |
| Vue d'ensemble | CPU, mémoire, disque le plus plein, charge, conteneurs actifs, points/s écrits, latence API p95, cibles en erreur |
| Hôte | CPU par mode, mémoire, charge, IO/s et débit disque (lecture/écriture), occupation et attente disque, réseau, systèmes de fichiers |
| Conteneurs | CPU, mémoire, IO disque et réseau par service ; tableau état, santé, redémarrages, ancienneté |
| Processus | Tous les processus de l'hôte regroupés par nom : nombre, CPU, mémoire, threads, IO, fichiers ouverts |
| API | Requêtes/s et latence p50/p95/p99 par route, erreurs 4xx/5xx, distribution des temps de réponse, pool de connexions, tâches de fond |
| Collecte OPC UA | Session OPC UA, écriture base, débit reçu/retenu/écrit, filtrage deadband, latence d'écriture, file d'attente, tampon disque, incidents |
| Base de données | Taille, connexions, transactions/s, cache hit, temps des requêtes SQL (pg_stat_statements), requêtes en cours, verrous, tables les plus volumineuses |
| Prometheus | État de chaque cible, séries et volume |

```bash
cd deploy
./mesctl monitoring on      # ajoute le profil monitoring à site.env et démarre Prometheus et les exporters
./mesctl monitoring off     # les arrête (les métriques déjà collectées restent dans le volume prometheus-data)
```

Prometheus collecte toutes les 10 s et garde `PROMETHEUS_RETENTION` (15 j) dans la limite de `PROMETHEUS_RETENTION_SIZE` (2 Go). Sur un poste de développement à l'étroit, baisser ce plafond dans `site.env`. Les métriques de l'API (`api:8000/metrics`) et du collecteur (`collector:9108`) ne sont joignables que depuis le réseau Docker. Le tableau se régénère avec `python3 grafana/generate/systeme.py`.

## Test de charge

```bash
python3 tests/loadtest.py --minutes 15
```

Résultats du lot 0 : [tests/results/charge-lot0-simulateur.md](tests/results/charge-lot0-simulateur.md). En résumé : 3 000 points par seconde pour 12 % d'un cœur côté collecteur, retard de collecte sous 2,3 s, environ 12 octets par point compressé, soit environ 1,3 To pour un an de données brutes de 10 000 tags dans le cas central.

## Arborescence

```
api/            API et traitements MES (Python, FastAPI) et leurs tests
catalog/        types de machines et types de recettes (YAML), livrés avec chaque version
collector/      collecteur OPC UA (Python, asyncua) et ses tests
db/             migrations SQL et outil migrate
deploy/         docker-compose.yml, site.env.example, mesctl, configuration de surveillance
grafana/        sources de données et tableaux de bord provisionnés
simulator/      simulateur S7-1500 et configurations de sites
docs/           documentation d'intégration automate
tests/          test de charge et résultats
web/            interface (Preact + htm, sans compilation) et configuration nginx
```

## Développement derrière un proxy d'entreprise

Les Dockerfile acceptent un certificat d'autorité optionnel pour `pip` :

```bash
docker build --secret id=ca,src=/chemin/ca.crt --build-arg HTTPS_PROXY=$HTTPS_PROXY collector
```
