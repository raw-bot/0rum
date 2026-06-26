# AK MACD — Runner Exit (variante à backtester, NON implémentée)

Statut : **SPEC ONLY**. Hypothèse R&D #1. Ne pas déployer en live avant backtest concluant.
Date : 2026-06-26.

## Motivation

Le TP fixe à 2R plafonne les *winners* à queue épaisse. Mesuré sur les 3 gagnants
post-cutover : après que le 2R a coupé, le prix a continué jusqu'à **4.12R, 4.09R,
2.47R** (2/3 sont allés à ~4R). Le plafond fixe a laissé ~+2R sur la table sur les
gros mouvements. C'est le plus gros poste de perte d'edge identifié.

## Garde-fous (non négociables)

1. **n insuffisant.** 3 trades ne prouvent rien. Prometteur ≠ prouvé. Logger
   systématiquement le comportement **après +2R** sur chaque gagnant (live + backtest).
2. **Le pic n'est pas capturable.** « Allé à 4R » ≠ « un système réel aurait capté 4R ».
   Il faut une **règle causale** simulée bougie par bougie (pas du hindsight sur le MFE).
3. **Le trigger “danger” doit être objectif.** Une règle claire, pas du feeling codé.

Principe directeur : **sécurité + patience, pas sécurité + peur.** Le plancher donne
la sécurité ; le trail doit être assez LARGE pour laisser respirer le runner (pas un
stop serré qui clippe à 2.1R).

## La variante (R mesuré depuis l'entrée ; risk = |entry − SL_initial|)

| Phase | Déclencheur | Comportement du stop |
|-------|-------------|----------------------|
| **0 — bracket initial** | entrée → +1.5R | SL initial figé. Si touché → −1R. (inchangé) |
| **1 — plancher** | le prix touche **+1.5R** | SL remonté à **+1.5R** garanti — ne rend jamais sous 1.5R |
| **2 — runner** | le prix touche **+2R** | **pas de TP fixe.** Mode trailing, pas de plafond. Ride 3R/4R+ tant que le trail tient |
| **Sortie** | trail touché OU danger objectif | sortie entre +1.5R et +∞ |

## Triggers “danger” objectifs (à backtester, un seul ou combo)

- **Trail ATR** : stop = plus_haut − k·ATR(14). Param `k` ∈ {2.0, 2.5, 3.0}. Sortie sur clôture sous le stop.
- **Trail structure** : stop = dernier swing low confirmé (pivot n-barres). Sortie sur clôture sous le swing.
- **Cassure MACD** : sortie si le MACD flippe contre la position (logique de flip déjà calculée).
- **Bougie de reversal** : sortie sur bougie opposée franche (corps ≥ X·ATR contre la position).
- **Cap de retracement** : sortie si retracement depuis le pic > X% (ex. 40%).
- **Contrainte commune** : le stop effectif ne descend JAMAIS sous +1.5R une fois la phase 1 atteinte.

Params à balayer : niveau plancher (1.5R), activation runner (2R), `k` ATR, lookback swing, retrace %, choix du trigger.

## Plan de backtest

- **Données : 1m** (résolution intra-bougie indispensable — le 15m ne peut pas résoudre l'ordre du trail).
- **Baseline** : bracket fixe 2R actuel.
- **Variantes** : la spec ci-dessus avec chaque trigger candidat.
- **Mêmes entrées** : on réutilise les signaux AK MACD existants ; **seule la sortie change**.
- **Métriques** : R net total, espérance, win rate, R moyen des gagnants, give-back max,
  distribution des R de sortie, et **efficacité de capture = R réalisé / MFE**.
- **Verdict** : la capture supplémentaire sur les gros mouvements dépasse-t-elle le give-back
  sur ceux qui se retournent ? **Le R net tranche**, pas l'anecdote.

## Logging (garde-fou 1, faisable maintenant, observation-only)

Sur chaque trade : enregistrer MFE post-2R et, en simulation, le R de sortie sous chaque
règle. Ne change aucune décision live → ne remet pas à zéro l'échantillon de validation.
