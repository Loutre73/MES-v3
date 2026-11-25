# Chargement des recettes dans l'automate (S7-1500)

Le MES n'écrit jamais directement dans les consignes actives. Il dépose la recette dans un
**tampon**, puis demande à l'automate de l'appliquer. L'automate garde la main : il accepte ou
refuse selon l'état de la machine. Ce document décrit le bloc à créer dans TIA Portal.

## Bloc de données (exemple `DB_Recette_Ligne`, non optimisé ou optimisé)

| Membre                 | Type        | Écrit par | Rôle                                              |
|------------------------|-------------|-----------|---------------------------------------------------|
| `Tampon.<paramètre>`   | Real / DInt | MES       | valeurs de la recette à charger                   |
| `Actif.<paramètre>`    | Real / DInt | automate  | valeurs en production (lues pour la comparaison)  |
| `Demande`              | Bool        | MES       | front montant = demande de chargement             |
| `Reponse`              | Int         | automate  | 0 attente, 1 acceptée, 2 refusée                  |
| `Code`                 | Int         | automate  | motif du refus (voir plus bas)                    |

Les noms des paramètres sont ceux de `catalog/recipes/ligne.yaml` (clé `nom`). Ajouter un
paramètre = l'ajouter dans le DB **et** dans ce fichier, puis livrer une version du MES.

Dans TIA Portal, cocher **Accessible depuis HMI/OPC UA** pour tout le DB, et **Écriture depuis
HMI/OPC UA** uniquement pour `Tampon`, `Demande` et `Reponse`.

## Échange

1. Le MES écrit toutes les valeurs dans `Tampon`.
2. Le MES écrit `Reponse := 0`, puis `Demande := TRUE`.
3. Sur front montant de `Demande`, l'automate contrôle :
   - machine dans un état qui autorise le changement (par exemple à l'arrêt ou en fin de lot),
   - chaque valeur dans ses bornes automate (les bornes du MES sont des bornes de saisie,
     l'automate reste le garde-fou),
   - aucun défaut bloquant.
4. Si tout est bon : copie `Tampon` → `Actif`, `Code := 0`, `Reponse := 1`.
   Sinon : `Actif` inchangé, `Code := motif`, `Reponse := 2`.
5. Le MES lit `Reponse` toutes les 300 ms pendant `delai_s` (10 s par défaut), enregistre le
   résultat dans l'historique de la recette, puis remet `Demande := FALSE`.

Codes de refus connus du MES (affichés en clair) : 1 machine en marche, 2 valeur hors bornes
automate, 3 défaut présent, autres : refus automate.

## Exemple SCL (FB appelé cycliquement)

```scl
// #Fp_Demande : Bool statique (mémoire de front)
IF "DB_Recette_Ligne".Demande AND NOT #Fp_Demande THEN
    IF "DB_Lignes".L1.Marche THEN
        "DB_Recette_Ligne".Code := 1;
        "DB_Recette_Ligne".Reponse := 2;
    ELSIF "DB_Recette_Ligne".Tampon.Vitesse_convoyeur > 95.0 THEN
        "DB_Recette_Ligne".Code := 2;
        "DB_Recette_Ligne".Reponse := 2;
    ELSE
        "DB_Recette_Ligne".Actif := "DB_Recette_Ligne".Tampon;   // copie de la structure
        "DB_Recette_Ligne".Code := 0;
        "DB_Recette_Ligne".Reponse := 1;
    END_IF;
END_IF;
#Fp_Demande := "DB_Recette_Ligne".Demande;
```

## Comptes OPC UA

Le MES peut utiliser un compte OPC UA distinct pour les écritures (`OPCUA_WRITE_USERNAME` /
`OPCUA_WRITE_PASSWORD` dans `site.env`), avec dans TIA Portal un rôle utilisateur limité à
l'écriture de ce DB. La collecte reste en lecture seule.

## Essai sans automate

Le simulateur fourni (`./mesctl install --demo`) implémente cet échange avec un refus si
`Vitesse_convoyeur` dépasse 95 % : utile pour tester un chargement accepté puis refusé.
