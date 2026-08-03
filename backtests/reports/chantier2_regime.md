# Chantier 2 — Détecteur de régime en shadow (walk-forward, seuils figés)

Date : 2026-07-17. Module `scripts/replay_harness/regime.py` + CLI `regime`,
run `backtests/runs/regime_v1/` (`verdicts.jsonl` = un verdict journalisé par
trade « aurait autorisé/bloqué, pourquoi » ; `regime_report.json`).
**Jamais branché sur les signaux live** — lecture seule des ledgers de replay
(`iso_ak`, `iso_utbot`, `iso_ha`) et des snapshots. Tests :
`tests/test_regime.py` (14), suite complète 863 verts.
Empreintes : data BTC 4h `6d47ce717c8215d9…`, config `62a3066fb9cb76d3…`.

## Protocole (strict, ex ante)

Features au timestamp d'entrée de chaque trade, uniquement sur bougies 4h
CLOSES (`SnapshotProvider.as_of`) : `channel_mid_slope_atr` (pente du milieu
du canal EMA20 high/low sur 5 barres, normalisée ATR14) et
`efficiency_ratio_10` (ER de Kaufman). Fenêtres calendaires : train 12 mois →
éval 6 mois, roulées de 6 mois (4 fenêtres, OOS = 2025-01 → 2026-07-15).
Seuils appris par grille de quantiles sur le train (objectif : E[R net]
conditionnel max sous contrainte ≥ 30 % des trades conservés), **FIGÉS**,
appliqués à la fenêtre suivante. Features indisponibles (warmup) → fail-open.

## Résultats OOS — E[R | régime détecté ex ante]

| Stratégie | n OOS | E[R] tous | E[R] autorisés | E[R] bloqués | gardés | Net sans filtre | Net filtré | Profits perdus | Pertes évitées |
|---|---|---|---|---|---|---|---|---|---|
| AK-MACD | 23 | +0,77 | +0,75 | +1,16 (n=1) | 96 % | 4 932 $ | 4 543 $ | 389 $ | 0 $ |
| HA trend | 42 | **−0,02** | **+0,38** | **−0,17** | 26 % | −193 $ | **+438 $** | 2 158 $ | 2 789 $ |
| UTBot | 97 | +0,89 | +0,71 | **+1,03** | 44 % | 10 414 $ | 2 925 $ | **20 639 $** | 13 150 $ |

(Balance walk contrefactuel à taille fixe, sans recomposition — approximation
documentée. DD du walk HA : 882 $ → 654 $ avec filtre.)

## Verdicts

1. **HA** : le détecteur fait exactement ce que la thèse « module
   conditionnel de tendance » prédisait — E[R] passe de −0,02 à +0,38 en ne
   gardant que 26 % des trades (11/42), win rate 36 % vs 23 %, net −193 $ →
   +438 $, DD réduit. Les seuils appris sont STABLES d'une fenêtre à l'autre
   (slope_min toujours positif, 0,10–0,46). C'est un vrai signal ex ante,
   mais l'échantillon OOS est modeste (42 trades) : qualification, pas
   preuve. Si HA devait revenir au budget un jour, ce serait UNIQUEMENT
   derrière ce détecteur — décision utilisateur, et après extension de
   l'échantillon.
2. **AK** : filtre inutile (96 % des trades gardés, le seul bloqué était un
   gagnant). Le filtre interne d'AK suffit ; ne rien ajouter.
3. **UTBot** : filtre NOCIF — les trades bloqués sont MEILLEURS que les
   autorisés (E[R] 1,03 vs 0,71) ; coût OOS −7 489 $. Le régime « tendance
   4h » n'est pas le conditionnement d'un momentum 15m/1h : l'apprentissage
   train (E[R] pass jusqu'à 2,72) ne généralise pas. **Ne jamais appliquer un
   filtre de régime uniforme au portefeuille.**

## Limites honnêtes

Échantillons par fenêtre train : 10–17 (AK), 27–35 (HA), 62–71 (UTBot) ;
grille volontairement grossière (8 quantiles × 2 features) pour limiter le
sur-ajustement ; conventions legacy des ledgers sources (fill au close) ; le
walk contrefactuel ignore la recomposition des tailles. V2 possible :
features multi-échelles pour UTBot (1h), mérite appris pour l'arbitre du
chantier 1, extension d'échantillon HA via re-runs sur fenêtres décalées.
