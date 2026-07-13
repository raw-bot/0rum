# Courbe historique continue de la prévision

**Date :** 2026-07-13  
**Statut :** conception approuvée par l’utilisateur

## Objectif

Le graphique de marché doit montrer une seule courbe jaune historique comparable
aux bougies réelles, comme sur la capture de référence. L’ancien overlay composé
de petits chemins jaunes par prévision et de chemins blancs « réel » est supprimé.
Les bougies sont l’unique représentation du prix réellement observé.

## Sémantique de la courbe

Chaque point jaune placé à l’horodatage cible `T` représente le prix médian p50
prévu 24 heures plus tôt pour `T`. La série est donc une comparaison à horizon
constant :

- jaune à `T` = `close(T-24h) * (1 + p50_24h calculé à T-24h)` ;
- bougie à `T` = prix réellement observé à `T`.

Les points sont reliés dans l’ordre chronologique pour former une seule courbe.
La projection future existante conserve ses repères +6 h, +12 h, +18 h et +24 h.
+18 h reste une interpolation visuelle entre les horizons entraînés +12 h et
+24 h.

## Origine et honnêteté des données

L’archive live n’existe que depuis le 13 juillet 2026 à 06:00 UTC. Pour fournir
immédiatement un historique utile, le moteur reconstruit la série antérieure en
walk-forward : pour chaque origine historique, le modèle ne peut entraîner ses
voisins que sur des cibles déjà closes à cette origine. Le prix prévu est ensuite
placé 24 heures plus tard, face à la bougie réellement survenue.

La reconstruction réutilise exactement la méthode
`walk_forward_nearest_regime`, le même nombre de voisins et la même profondeur
de marché que la prévision opérationnelle. Elle expose les 168 dernières cibles
horaires, soit sept jours.

Quand une réalisation +24 h issue de l’archive live devient disponible, elle
remplace le point reconstruit du même horodatage cible. Le journal append-only
reste la source prioritaire pour les prévisions effectivement enregistrées.
La provenance (`walk_forward` ou `live_archive`) reste dans les données pour
l’audit, mais n’ajoute pas une seconde courbe.

## Contrat de données

Le rapport de prévision ajoute `history_24h`, ordonné du plus ancien au plus
récent. Chaque point contient :

- `origin_ts` ;
- `target_ts` ;
- `predicted_price` ;
- `actual_price` ;
- `median_return` ;
- `median_error` ;
- `source`.

Le dashboard transmet cette série sous la carte de stratégie correspondante et
fusionne les réalisations live par `target_ts`. Aucun calcul de prévision n’est
dupliqué dans le navigateur.

## Rendu

- une polyline jaune continue est dessinée uniquement sur la zone historique ;
- les bougies restent le réel ;
- aucune polyline blanche n’est dessinée ;
- les anciens segments jaunes par origine sont supprimés ;
- la légende « JAUNE 50% = PRÉVU · BLANC = RÉEL » est supprimée ;
- le rail indique sobrement `Historique prévision +24 h`, sans créer de contrôle
  de navigation ou de sélection ;
- si la série est absente ou invalide, le graphique continue sans elle et la
  projection future reste disponible.

## Vérification

Les tests doivent prouver :

1. qu’un point walk-forward n’utilise aucune cible postérieure à son origine
   pour l’entraînement ;
2. que `predicted_price` est aligné sur `target_ts = origin_ts + 24 h` ;
3. que la série est chronologique et limitée aux 168 dernières cibles ;
4. qu’un point live remplace le point reconstruit de même cible ;
5. que le SVG contient une seule courbe historique jaune et aucun ancien chemin
   blanc/segmenté ni ancienne légende ;
6. que la projection future +6/+12/+18/+24 h reste inchangée.

## Risques et retour arrière

Le calcul walk-forward est plus coûteux que le seul rapport courant. Il reste
dans la fonction pure de prévision déjà exécutée par le moteur et sa sortie est
bornée à 168 points. En cas de régression, le champ `history_24h` et sa polyline
peuvent être retirés sans toucher aux décisions, aux positions, aux fills ou au
journal live.
