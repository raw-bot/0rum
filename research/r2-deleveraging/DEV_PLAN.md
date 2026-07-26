# R2 — Plan de développement (phase d'entraînement déclarée) — gelé le 2026-07-26

Ce fichier est committé AVANT toute analyse des données de développement. La phase de développement est un **entraînement assumé** : tout ce qui y est exploré a un coût de sélection, comptabilisé par la séparation stricte venue+temps (confirmation : Kraken 2024-01→2026-07, intacte).

## Données de développement

Binance BTCUSDT & ETHUSDT, 2020-09 → 2023-12 : metrics 5 min (sum_open_interest), klines 1 min (OHLC, volume, taker buy volume → flux signé), funding 8 h.

## Espace de recherche autorisé (grilles fermées — rien d'autre ne sera exploré)

- Chute extrême : rendement sur {15, 30, 60} min < {−2 %, −3 %, −4 %} OU < percentile roulant 90 j {p0.5, p1}.
- Volume : volume 5 min > {3, 5, 10} × médiane roulante 90 j.
- Contraction OI : ΔOI sur {30, 60} min < percentile roulant 90 j {p1, p2.5, p5}. OI laggé d'une fenêtre complète de publication (5 min).
- Entrée : close de la première bougie 1 min positive après réunion des conditions — aucune confirmation ultérieure, aucun critère utilisant le futur.
- Cooldown (fixé ex ante, hors grille) : pas de nouvel épisode < 12 h, sauf retour préalable de la vol 1 h sous sa médiane 90 j. BTC et ETH simultanés (< 2 h d'écart) = un seul épisode.
- Horizon primaire : **2 h** (fixé par prereg §6). 30 min et 4 h : descriptifs.

## Benchmark obligatoire (le discriminant)

Mêmes conditions SANS la contraction OI, épisodes appariés sur : décile de magnitude de chute, décile de vol réalisée, décile de volume relatif, régime funding (z<−1 / neutre / z>+1). La statistique décisive est Δ = E[R2h | avec OI] − E[R2h | sans OI, apparié].

## Coûts (développement)

Taker 5 bp + fill au pire prix de la bougie d'entrée + spread modélisé croissant avec la vol réalisée 5 min. (La confirmation utilisera les frais Kraken, plus élevés.)

## Critères d'abandon EN développement (avant toute confirmation)

R2 est abandonné — et les données Kraken ne seront jamais analysées — si, sur la meilleure cellule de grille :

1. N épisodes < **25** (clusterisés) sur 2020-09→2023-12 ; ou
2. Espérance nette à 2 h ≤ 0 ; ou
3. **Δ (contribution OI) < 10 bp net par événement** ; ou
4. Δ concentré : > 50 % de Δ provient des 3 meilleurs épisodes ; ou
5. Δ de signe instable entre 2020-21 et 2022-23.

Si le développement passe : gel des paramètres dans `PREREG_CONFIRM.md` (committé avant tout run Kraken), puis run confirmatoire unique.

## Ce que le développement ne peut PAS faire

- Modifier l'horizon primaire, le cooldown, la définition d'entrée, ou δ = 10 bp.
- Tester des variables hors grilles ci-dessus (le funding n'est QUE la variable de stratification du matching).
- Regarder les données Kraken 2024+.
