# MES v3

MES standard pour automates Siemens S7-1500 en OPC UA : historian à 1 s sur changement, suivi de production, arrêts, alarmes, dérives moteurs, énergie et recettes. Le même produit se déploie sur chaque site ; tout ce qui est propre à un site est de la configuration (`deploy/site.env`) ou de la donnée (catalogue de tags).

Documents de conception : [comparaison des architectures](https://claude.ai/artifact/KLxPmR3DEiuxjA5J1ddfvc) · [architecture détaillée](https://claude.ai/artifact/KyNtLLU61mcpawd3Cti1yq)

## État : lot 0, socle technique

| Service | Rôle |
| --- | --- |
| `db` | PostgreSQL 16 + TimescaleDB : historian, catalogue, données MES |
| `migrate` | Applique les migrations SQL (`db/migrations`) et les durées de conservation, à chaque démarrage |
| `collector` | Client OPC UA : abonnements à 1 s, deadband par tag, écriture par lots, tampon disque, rechargement du catalogue à chaud |
| `grafana` | Tableaux de bord livrés en fichiers (`grafana/dashboards`) |
| `simulator` | Simulateur S7-1500 (profil `sim`) : 10 000 tags répartis en DB de moteurs, compteurs, lignes et mesures |
| `prometheus` | Surveillance du collecteur (profil `monitoring`) |

Ce qui arrive ensuite : catalogue par types de machines et découverte automatique (lot 1), arrêts et shifts (lot 2), alarmes (lot 3), dérives moteurs (lot 4), recettes (lot 5), énergie et performance (lot 6).

## Démarrage rapide (démonstration avec simulateur)

Prérequis : Linux, Docker et Docker Compose v2.

```bash
cd deploy
./mesctl install --demo     # construit les images, démarre la pile et le simulateur
./mesctl status             # tags actifs, points/s, retard de collecte, taille de l'historian
```

Grafana répond sur le port 3000 (utilisateur `admin`, mot de passe dans `deploy/site.env`). Deux tableaux de bord : **Collecte - santé** et **Historian - tendances**.

## Installation sur un site

```bash
cd deploy
./mesctl install            # crée site.env avec des mots de passe générés
# renseigner OPCUA_ENDPOINT, la sécurité OPC UA et BROWSE_ROOT dans site.env
./mesctl install            # récupère les images de MES_VERSION et démarre
```

Au premier démarrage, le collecteur parcourt l'automate sous `BROWSE_ROOT` (par défaut `PLC_1/DataBlocksGlobal`) et crée un tag par variable numérique ou booléenne. Les deadbands se règlent dans `catalog.tag` (`deadband_kind` = `none`, `absolute` ou `percent`) et sont prises en compte sans redémarrage.

Pour un site sans Internet : `./mesctl package 0.1.0` sur un poste connecté, copier l'archive, puis `./mesctl update 0.1.0 --offline mes-images-0.1.0.tar`.

## Mises à jour

```bash
./mesctl update 0.2.0       # sauvegarde, nouvelles images, migrations, redémarrage, contrôle de santé
./mesctl rollback           # version précédente + restauration de la sauvegarde d'avant mise à jour
./mesctl backup             # sauvegarde complète (rotation selon BACKUP_KEEP)
```

Le collecteur redémarre en dernier. Pendant une coupure de la base, il garde les valeurs dans son tampon disque (`/spool`) et les réécrit dès que la base répond.

Les versions sont publiées par la CI : un tag Git `v0.2.0` construit et pousse `ghcr.io/loutre73/mes-*:0.2.0`.

## Historian

- `historian.value` : une ligne par changement (`time`, `tag_id`, `value`, `quality` 0 bon / 1 incertain / 2 mauvais). Les booléens valent 0 ou 1.
- Chunks de 6 h compressés après 12 h, conservation `HISTORIAN_RETENTION` (1 an par défaut).
- `historian.value_1min` : agrégats à la minute (moyenne, min, max, dernière valeur), compressés, conservation `AGGREGATE_1MIN_RETENTION` (1 an).
- `historian.value_1h` : agrégats horaires calculés depuis les agrégats minute, conservation `AGGREGATE_1H_RETENTION` (3 ans).
- Entre deux changements, la valeur est la dernière connue : `historian.value_at(tag_id, instant)`, ou `locf()` dans les requêtes Grafana. Le collecteur réécrit la dernière valeur des tags immobiles toutes les 15 min pour borner ces recherches.
- Une perte de connexion à l'automate écrit une valeur de qualité mauvaise pour chaque tag.

## Test de charge

```bash
python3 tests/loadtest.py --minutes 15
```

Résultats du lot 0 : [tests/results/charge-lot0-simulateur.md](tests/results/charge-lot0-simulateur.md). En résumé : 3 000 points par seconde pour 12 % d'un cœur côté collecteur, retard de collecte sous 2,3 s, environ 12 octets par point compressé, soit environ 1,3 To pour un an de données brutes de 10 000 tags dans le cas central.

## Arborescence

```
collector/      collecteur OPC UA (Python, asyncua) et ses tests
db/             migrations SQL et outil migrate
deploy/         docker-compose.yml, site.env.example, mesctl, configuration de surveillance
grafana/        sources de données et tableaux de bord provisionnés
simulator/      simulateur S7-1500 et configurations de sites
tests/          test de charge et résultats
```

## Développement derrière un proxy d'entreprise

Les Dockerfile acceptent un certificat d'autorité optionnel pour `pip` :

```bash
docker build --secret id=ca,src=/chemin/ca.crt --build-arg HTTPS_PROXY=$HTTPS_PROXY collector
```
