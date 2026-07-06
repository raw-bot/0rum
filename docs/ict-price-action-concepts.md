# ICT / Price-Action Concepts — Reference + Mapping to 0rum

Statut : **REFERENCE + R&D**. Les concepts sont documentés tels que fournis ; la
section "Mapping" et "Plan de test" disent comment (et SI) on les intègre. Règle
maison : un concept n'entre dans la logique de décision **que s'il améliore le PF
de façon mesurable et consistante en walk-forward** (sinon on le laisse en doc et
on ne l'implémente pas).

Date : 2026-06-30.

---

## 1. Concepts (résumé opérationnel)

### BOS — Break of Structure (continuation)
Le prix casse une structure **dans le sens de la tendance**.
- Uptrend : casse un précédent plus-haut → acheteurs en contrôle → chercher longs sur pullback.
- Downtrend : casse un précédent plus-bas → vendeurs en contrôle → chercher shorts sur pullback.
- BOS = **feu vert de continuation**. Une tendance saine enchaîne les BOS.

### CHoCH — Change of Character (avertissement de retournement)
Le prix casse la structure **contre la tendance** pour la première fois.
- Uptrend : cassure sous le dernier higher-low → 1er warning baissier.
- Downtrend : cassure au-dessus du dernier lower-high → 1er warning haussier.
- CHoCH = **warning, pas confirmation**. On réduit/arrête l'ancienne direction ;
  on attend un **nouveau BOS** dans la nouvelle direction pour confirmer.

Séquence de retournement : tendance → BOS… → 1er CHoCH → pullback (FVG/OB) →
nouveau BOS → nouvelle tendance confirmée.

### Fibonacci pullback — profondeur = force de tendance
| Zone | Lecture | Action |
|------|---------|--------|
| 23.6–38.2 % | pullback shallow, tendance très forte | entrée difficile, attendre |
| 50 % | pullback moyen, tendance saine | entrée correcte avec confluence |
| **61.8–78.6 % (OTE)** | deep pullback, sweet spot (~70.5 %) | **meilleure zone d'entrée**, chercher FVG+rejet |
| 78.6–88 % | très profond, tendance affaiblie | risque élevé, forte confluence requise |
| > 100 % | structure cassée | ce n'est plus un pullback → reversal/invalidation |

### OTE — Optimal Trade Entry
Zone Fib **61.8–78.6 %**. Idéal = OTE + FVG dans la zone + entrée au CE.
- Long : Fib swing_low→swing_high, pullback en zone, FVG, entrée CE, SL sous 78.6/invalidation, cible = high précédent puis extension.
- Short : Fib swing_high→swing_low, miroir.

### FVG — Fair Value Gap
Imbalance / inefficience : espace vide entre bougies (gap sans mèche de recouvrement
sur 3 bougies). Le prix tend à revenir le combler. Sert de **zone précise de pullback**
dans l'OTE. Confluence additionnelle, jamais signal isolé.

### CE — Consequent Encroachment
**Milieu (50 %) du FVG.** Ligne d'entrée exacte quand le prix revient dans le FVG.

### Stack de confluence "A+"
1. Prix dans OTE 61.8–78.6 · 2. FVG dans l'OTE · 3. CE identifié · 4. bougie de rejet ·
5. aligné au biais HTF · 6. pendant une kill zone · 7. SL sous 78.6/invalidation ·
8. cible high/low précédent puis extension.
→ OTE seul = bon · +FVG = mieux · +CE+rejet+biais HTF+timing = A+.

### Garde-fous
- > 100 % retracement = plus un pullback. · Cassure contre tendance = CHoCH, pas BOS.
- Ne pas trader un reversal comme une continuation. · Pullback sans confluence = faible.

---

## 2. Mapping vers le système 0rum (AK MACD)

Ce que notre brain fait DÉJÀ, vu sous l'angle ICT :
| Notre logique | Équivalent ICT |
|---------------|----------------|
| `_sequenced_long/short` (tendance couleur → pullback → reprise) | approximation grossière de **BOS + pullback** |
| `flip_up/down` MACD + confirmation | déclencheur de momentum (pas structurel) |
| `regime_filter` (rolling return 20) | proxy faible de **biais HTF** |
| bracket SL/TP figé, RR 1.5/2.0 | sizing, pas ICT |

**Ce que ICT a et qu'on n'a PAS** (les candidats à tester) :
1. **Biais HTF explicite** — ne trader QUE dans le sens d'une tendance de timeframe
   supérieur (4H/Daily). C'est le plus prometteur : mes folds montrent que le PF
   varie 0.44→1.08 selon la période → un filtre directionnel macro est LE levier
   suspecté. ICT formalise ça (BOS continuation + biais HTF).
2. **Kill zones** — ne trader que sur les fenêtres horaires actives (London/NY).
3. **OTE depth** — filtrer par profondeur de pullback (rejeter shallow < 38 % et
   trop-profond > 88 %, favoriser 61.8–78.6).
4. **FVG / CE** — raffinement de localisation d'entrée (structurel, coûteux, phase 2).

**Ce qu'on a déjà PROUVÉ non-levier** (Levers A + C) : entrée arm-fire vs lâche
(identique), exit runner vs fixed (runner pire), relâchement de filtres (bruit).
Donc on NE re-teste PAS l'exit ni les filtres existants. On teste les **filtres
directionnels/temporels** ICT qui n'existent pas chez nous.

---

## 3. Plan de test (ordre par rapport coût/levier)

| # | Filtre ICT | Implémentable ? | Prior |
|---|-----------|-----------------|-------|
| 1 | **Biais HTF** (entrée alignée à la tendance 4H) | facile (agrég. 15m→4H + EMA) | élevé |
| 2 | **Kill zone** (heures London+NY) | trivial (heure UTC du bar) | moyen |
| 3 | **OTE depth** (pullback 61.8–78.6 du dernier swing) | moyen (détection de swing) | moyen |
| 4 | BOS/CHoCH structurel, FVG, CE | coûteux (pivots, gaps) | phase 2 si 1-3 prometteurs |

**Protocole** : chaque filtre = une variante one-variable appliquée aux entrées
arm-fire existantes, mesurée sur 50k barres BTC 15m + walk-forward 5 folds, contre
la baseline PF 0.80. Gate "vraie amélioration" : PF monte ET reste > baseline sur
la majorité des folds. Résultats consignés dans `state/ict_filter_result.txt`.

> Verdict attendu honnête : si aucun filtre ne franchit PF 1.0 de façon consistante,
> on conclut que l'habillage ICT n'apporte pas d'edge sur BTC 15m, on garde ce doc
> en référence, et on n'implémente rien en live.

---

## 4. RÉSULTATS (test exécuté 2026-06-30, BTC 15m, 50k bars, walk-forward 5 folds)

Script : `scripts/experiment_ict_filters.py` · sortie : `state/ict_filter_result.txt`

| Filtre | trades | PF | net | maxDD | verdict |
|--------|--------|----|-----|-------|---------|
| baseline (arm-fire) | 505 | 0.80 | −$6,458 | 89% | référence |
| **htf_bias** | 349 | **0.81** | −$4,110 | **56%** | PF plat, mais DD ÷1.6 |
| killzone | 255 | 0.73 | −$4,109 | 50% | pire (PF) |
| ote_depth | **3** | 3.46 | +$149 | 1% | **inexploitable** (incompatible) |
| htf + killzone | 156 | 0.65 | −$3,383 | 39% | pire (PF) |

Walk-forward `htf_bias` PF : 0.62 / 0.59 / 1.07 / 0.91 / 0.93 → bat la baseline 3 folds/5,
**inconsistant**, ne crée pas d'edge stable.

### Conclusions (appliquées)
1. **ICT-en-filtre n'apporte PAS d'edge** : `htf_bias` garde PF ≈ 0.80→0.81. Il coupe
   les trades à contre-tendance → perd MOINS (net et surtout **drawdown 89%→56%**),
   mais c'est de la réduction de risque, pas de la rentabilité. → **rien intégré en live.**
2. **`htf_bias` a une vraie valeur défensive** (DD ÷1.6) : à réactiver SI la stratégie
   devenait rentable un jour. Noté, pas déployé.
3. **OTE / FVG / CE sont incompatibles avec nos entrées** : 3 trades sur 1.4 an. Nos
   entrées arm-fire sont du **momentum (flip MACD)**, pas des **deep-pullbacks**. ICT
   au cœur = un **modèle d'entrée DIFFÉRENT** (BOS→OTE→FVG→CE), pas un filtre.

### Ce qui reste non testé (option, pas une dette)
Le **vrai ICT** = remplacer le cerveau d'entrée par un modèle structurel (détection
swings/BOS/CHoCH, entrée sur pullback OTE dans un FVG au CE), pas l'habiller. C'est
un *rebuild*, évalué seulement si on décide d'investir cette direction. Coût élevé
(détection de pivots fiable), prior incertain sur BTC 15m vu que le momentum-entry
n'a pas d'edge ici.
