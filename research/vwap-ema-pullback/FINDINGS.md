# VWAP-EMA pullback (reframed @austin.daytrades) — BTC 4h first cut

_Date: 2026-07-06. Data: BTC 5m→4h resample, 2020-09-01 → 2026-06-29 (12 768 barres).
Costs in: fee 5 bps/side + slip 3 bps (0rum DEFAULT_EXIT). Long-only. ATR bracket 1.5/2.0, max_hold 96._

## Question
L'idée transférable du reel (entrer sur un pullback vers une MA rapide dans le sens d'un
ancrage pondéré volume) a-t-elle une espérance positive nette sur BTC 4h, une fois recodée
proprement (ancrage crypto = rolling-VWAP, pas session-VWAP) et jugée avec le harness 0rum ?

## Résultat (signal ema9 / vwap50)
| | n | net (R, 1%/trade) | PF | win | exp_R | maxDD |
|---|---|---|---|---|---|---|
| full | 507 | +18.1% | 1.13 | 47% | +0.040 | -20% |
| train (70%) | 349 | +14.2% | 1.16 | 47% | +0.045 | -20% |
| **OOS (30%)** | 159 | +2.3% | **1.03** | 47% | +0.021 | -13% |

- **Random-entry baseline** : bat **99 %** des 150 sims aléatoires (même exit/coûts). ⇒ le *timing du
  signal* apporte de la valeur, pas seulement l'exit rule dans ce régime.
- **Outlier-dominance** (drop top 2) : PF reste 1.09, **non dominé**.
- **Benchmark Donchian-20 long** : PF 0.97, **outlier-dominé**, OOS négatif → le pullback **bat le
  benchmark obligatoire**.

## Grille de paramètres (full sample, aucune sélection)
Positif sur ~15/16 cellules (PF 1.04–1.19). Signal net : **plus l'ancrage est lent, mieux c'est** —
`vwap=200` domine partout (exp_R +0.069→+0.076, maxDD ~-10%) vs `vwap=30` (exp_R ~+0.01, maxDD -20/-29%).
⇒ confirme que la **VWAP-session rapide du reel était le mauvais ancrage pour le crypto** ; un ancrage
de tendance lent est l'edge réel.

## Verdict
Edge **réel mais mince**, et **décroît en OOS** (PF ~1.03). Mieux que la plupart des reels (bat random +
Donchian + survit au drop d'outliers), mais **pas déployable seul** sur 0rum en l'état.

## Porte 1 — validation multi-actifs SANS re-tuning (2026-07-07) → **ÉCHOUE**
Données 4h `data.binance.vision`, mêmes params, aucun re-tuning (`multi_asset.py`).
Corrélation daily-returns : BTC↔ETH **0.81** (même famille = 1 seul pari), PAXG/or **0.12** (décorrélé réel).

| params | BTC | ETH | **PAXG/or (décorrélé)** |
|---|---|---|---|
| ema9/vwap50 | expR +0.023 (bat 99% rand) | +0.001 (mort) | **-0.068, net -30.7%, maxDD -40%** |
| ema9/vwap200 | +0.034 (bat 98% rand) | +0.019 | **-0.051, net -21%** |

**Verdict** : la règle exige la rentabilité sur ≥2 actifs **décorrélés** sans re-tuning. Ici l'edge
existe sur BTC, fuit vers ETH (corrélé) avec l'ancrage lent, mais **perd franchement sur l'or**. C'est
donc un **artefact de momentum long-only spécifique au crypto**, pas un edge structurel universel.
La règle anti-overfit n°1 le **rejette comme stratégie multi-actifs autonome**. (Bon résultat : le
garde-fou a fait son travail.)

## Recyclage — ce qui reste vrai
L'edge crypto est **cohérent** (BTC +0.034, ETH corrobore +0.019, bat ~98% des tirages aléatoires) →
c'est un signal **conditionnel au régime de tendance crypto**, pas universel. Il ne devient utile que
**gaté aux fenêtres de tendance crypto** (porte 2), et **uniquement côté crypto** (jeter la jambe or).
Au mieux : un *raffinement d'entrée* pour le cerveau long-only crypto existant pendant les régimes
trending — à tester **contre l'AK MACD**, pas en remplacement.

## Gate #3 — MCPT in-sample (neurotrader888/mcpt, vendé+adapté) → **SIGNIFICATIF**
`mcpt_test.py`. Ré-optimise la **même grille 16 cellules** sur 199 séries BTC permutées
(permutation de barres en log-espace : intrabar h/l/c + volume mélangés ensemble, gaps à part —
préserve mean/std/skew/kurt par barre, détruit la séquence temporelle). Métrique = Profit Factor.

- Real best PF = **1.195** (ema13/vwap200). Permuté : médiane 1.009, p90 1.083, **max 1.173**.
- **p-value = 0.005** (aucune des 199 permutations n'atteint le PF réel).
- ⇒ Le « gagnant de grille » **n'est PAS du noise-mining** : optimiser aussi fort sur du BTC aléatoire-
  mais-réaliste n'y arrive jamais. Il y a une **vraie structure temporelle exploitable sur BTC**.

**Réconciliation (important)** : significatif ≠ déployable. Trois portes, trois réponses cohérentes :
1. bat les entrées aléatoires (99%) ✓ · 2. MCPT in-sample significatif (p=0.005) ✓ · 3. généralise à un
actif décorrélé sans re-tuning ✗. L'edge est **réel mais spécifique au régime de tendance crypto** et
**mince en OOS** (PF~1.03). MCPT confirme qu'il y a « quelque chose » à gater ; la porte 3 confirme qu'on
ne le déploie pas seul, multi-actifs. Prochain MCPT logique : **walk-forward** (permuter seulement l'OOS,
params figés) pour mettre une p-value sur la magnitude OOS, pas juste sur l'optimisation in-sample.

## Porte 2 — gate de régime + comparaison à l'AK MACD (2026-07-07)
`door2_compare.py` (les 3 signaux long-only dans LE MÊME moteur d'exit ATR, mêmes frais).

**a) Le gate `rolling_return_regime` du projet n'aide PAS le pullback.**
| config | RAW expR (OOS PF) | + GATE expR (OOS PF) |
|---|---|---|
| ema9/vwap50 | +0.023 (1.00) | **-0.002 (0.84)** ← détruit l'edge |
| ema9/vwap200 | +0.034 (0.94) | +0.040 (0.94) ← neutre, 0 gain OOS |
Raison : un pullback-dans-le-creux se déclenche souvent juste après une baisse → le classifieur
étiquette « unfavorable » et **bloque exactement les bonnes entrées**. C'est le mauvais gate pour ce
signal (il a été taillé pour les entrées momentum-continuation de l'AK).

**b) Incumbent AK MACD — piège d'exit, puis vrai chiffre.**
Entrées AK forcées dans MON bracket ATR : PF 0.78, expR -0.088, bat 47% du random (≈ random) → **faux
négatif** : ça jette l'exit de l'AK. Avec **son propre exit** (`backtest_4h_validation.py`, runner trail
floor 1.5R + ATR·2.5), **long-only** :
- Full : 104 tr, win 51%, **PF 1.53**, +28.2R · OOS (≥2024) : 52 tr, win 58%, **PF 2.02**, +23.7R
- Par an (long runner) : 2021 **0.67**, 2022 1.18, 2023 1.69, 2024 3.33, 2025 1.28, 2026 3.35 → consistant,
  seul 2021 rouge. L'edge de l'AK vit dans **la géométrie de sortie** (confirme ta réserve du PLAN).

**Verdict porte 2** : (1) le gate de régime ne sauve pas le pullback ; (2) le pullback **ne bat pas**
l'AK MACD. Même à son mieux (slow anchor, raw) il est OOS-plat/négatif via un bracket générique, alors
que l'AK long-only fait PF 1.53/2.02 avec son runner. *Caveat commensurabilité* : moteurs & barres un
peu différents (R-space vs equity fractionnaire ; spot REST vs um-zips), mais la direction est nette.

## CONCLUSION — arrêter de pousser le pullback
Idée légitime et bien testée (edge BTC réel, **MCPT-significatif p=0.005**), mais elle **échoue 3 portes
sur les bonnes raisons** : multi-actifs ✗, gate de régime n'aide pas ✗, ne bat pas l'incumbent ✗. Ce
qu'on garde de l'exercice : (a) **le gate MCPT** `get_permutation` à intégrer au harness (3e garde-fou),
(b) la confirmation que **l'edge de l'AK est réel et piloté par l'exit** — donc le levier d'amélioration
de 0rum est côté **sorties/gestion**, pas côté nouvelles entrées.

## Portes suivantes (avant tout branchement)
1. **Multi-actifs sans re-tuning** (ETH + PAXG/or) — règle anti-overfit n°1. Bloqué : données 4h ETH/PAXG
   absentes de `processed/`. À télécharger d'abord.
2. **Gating « période propice »** (thèse centrale du projet : l'edge est dans le QUAND). Non gaté 24/7
   c'est marginal ; à combiner avec les détecteurs de régime.
3. Tester la sortie « clôture au travers de l'EMA » du reel vs le bracket ATR (comparabilité).

## Repro
`../liquidation-continuation/.venv/bin/python run.py`
