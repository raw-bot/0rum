# Dashboard V5 modulaire — design validé

## Résultat attendu

Le dashboard expose trois cartes marché de même niveau : BTC/USDT, ETH/USDT et OR/PAXG. BTC est visible par défaut ; ETH et OR peuvent être ouverts ou fermés depuis une barre « Marchés ». Les trois cartes utilisent exactement le même moteur de rendu V5, tandis que le portefeuille paper reste unique.

## Cartes et agencement

- Chaque marché est une carte GridStack autonome.
- Chaque carte peut être déplacée et redimensionnée horizontalement et verticalement.
- Un bouton de fermeture masque la carte sans perdre les données.
- La barre Marchés permet de la rouvrir.
- La préférence d'affichage et la disposition sont persistées dans `localStorage`.
- L'ancienne carte `Trade Signals · Pine → 0rum` et les anciens mini-graphiques BTC/ETH/OR sont retirés du DOM et du cycle de rendu.

## Géométrie du graphique

- Le SVG est recalculé avec la largeur et la hauteur réelles de son conteneur.
- Aucun `preserveAspectRatio="none"` n'est utilisé dans les cartes V5.
- Les traits reçoivent `vector-effect: non-scaling-stroke` afin de rester proches d'un pixel pendant un redimensionnement.
- La zone prix, les volumes, le momentum et la timeline se répartissent proportionnellement dans la hauteur disponible.
- Le zoom interne change le nombre de bougies visibles ; il ne transforme pas le SVG.

## Navigation

- Boutons `−`, `+` et `1:1` dans chaque carte.
- Molette sur le graphique : zoom avant/arrière autour de la fenêtre récente.
- Limites : 36 à 220 bougies, 120 par défaut.
- Une indication visible affiche le nombre de bougies et le timeframe d'observation.

## Événements

- Chaque IN, TP et SL visible reçoit un libellé, une ancre sur le prix et une tige verticale jusqu'à la timeline.
- Chaque trade apparié reçoit une liaison IN→TP/SL verte ou rouge.
- Les marqueurs paper réels sont prioritaires ; les marqueurs modèle restent plus discrets.
- Aucun niveau de SL/TP ouvert n'est inventé lorsque le ledger ne le fournit pas.

## Style

- Fond anthracite bleuté, panneaux légèrement plus clairs et grille froide.
- Vert = hausse/entrée/gain ; rouge = baisse/SL/perte ; jaune = scénario d'affichage.
- Typographie compacte et traits fins sans décoration gratuite.

## Sécurité

- Aucun changement dans l'exécution, les stratégies, les ordres ou le ledger paper.
- Le changement est limité aux assets HTML/CSS/JS du dashboard.
- La sauvegarde `backups/dashboard-pre-v5-20260711/` reste le rollback complet.

