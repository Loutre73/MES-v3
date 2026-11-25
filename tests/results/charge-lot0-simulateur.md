# Test de charge du socle MES v3 (2026-11-24 22:03)

Durée : 15 min · tags surveillés : 10000 · lignes écrites pendant le test : 2767095

## Collecte

| Mesure | Valeur |
| --- | --- |
| Points écrits par seconde (moyenne) | 2966 |
| Points écrits par seconde (min / max) | 2729 / 3079 |
| Retard de collecte moyen | 1.24 s |
| Retard de collecte maximal | 2.24 s |

## Stockage

| Mesure | Valeur |
| --- | --- |
| Octets par point, non compressé | 112.1 |
| Octets par point, compressé | 9.8 |
| Points par jour à ce débit | 256 millions |
| Disque par jour (compressé) | 2.50 Go |
| Disque par an (compressé) | 913 Go |
| Chunk du jour non compressé (2 jours en ligne) | 57.5 Go |

## Charge des conteneurs

| Conteneur | CPU moyen (% d'un cœur) | CPU max | RAM moyenne (Mio) |
| --- | --- | --- | --- |
| collector | 12 | 19 | 57 |
| db | 4 | 29 | 292 |
| grafana | 0 | 1 | 56 |
| simulator | 26 | 61 | 218 |

## Mesure complémentaire sur une journée complète

Le test de 15 min sous-estime la compression (peu de valeurs par tag dans chaque chunk) et ne mesure pas les agrégats. Une journée synthétique a donc été générée : 1 000 tags, 30 % de changements par seconde, valeurs bruitées à 2 décimales, horodatages à la milliseconde.

| Mesure | Valeur |
| --- | --- |
| Points sur la journée | 25,9 millions |
| Octets par point, non compressé | 144 |
| Octets par point, compressé | 14,0 |
| Agrégats minute, octets par ligne compressée | 35 |

Les données réelles du simulateur ont donné 9,8 octets par point compressé. Le dimensionnement retient **12 octets par point**.

## Volume disque pour 10 000 tags, cas central (3 000 points par seconde)

| Élément | Conservation | Volume |
| --- | --- | --- |
| Données brutes compressées | 1 an | environ 1,1 To |
| Fenêtre non compressée (chunks de 6 h, compressés après 12 h) | 12 à 18 h | environ 20 Go |
| Agrégats minute compressés | 1 an | environ 185 Go |
| Agrégats horaires compressés | 3 ans | environ 10 Go |
| **Total base** | | **environ 1,3 To** |

Avec 10 % de changements par seconde (1 000 points par seconde), le total tombe à environ 0,6 To. Les deadbands réglées par type de tag sont donc le principal levier sur le disque.

## Conclusion

- Le collecteur tient 10 000 tags et 3 000 points par seconde avec 12 % d'un cœur et moins de 100 Mio de RAM. Le retard de collecte reste sous 2,3 s.
- La base n'est pas un goulot : 4 % d'un cœur en moyenne.
- Le disque est le poste à surveiller : prévoir **2 To** de stockage de données sur la VM (extensible), sauvegardes à part.
