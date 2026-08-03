# Chantier 1 — Arbitre budget-par-thèse (rapport avant/après)

Date : 2026-07-17. Harnais uniquement (`scripts/replay_harness/arbiter.py`),
**aucun code de prod modifié**. Fenêtre 2024-01-01 → 2026-07-15, 88 897 cycles
15m, config `backtests/configs/duo_ak_utbot.yaml` (duo cible, gate désactivé),
`--gate fast`. Données : sha256 `d5efff8d2d9461ad1f1ab189b01b0bdfa65cf3d3f1e72a36e3ebdcc3a4893571`
(identiques aux runs de référence). Suite de tests : 849 verts (837 + 12
nouveaux `tests/test_arbiter.py`).

## Mécanisme

À chaque cycle, TOUTES les intentions d'entrée sont collectées puis le budget
de risque par (symbole, direction) est alloué en ordre de mérite (v1 : rang
statique `--merit`, défaut ordre de config) — une enchère, pas un guichet.
Un signal sur une thèse déjà partiellement financée reçoit
`min(demande, budget restant)` (top-up) ; thèse pleine → refus
`thesis_already_funded` ; cap portefeuille liant → `risk_cap_total`.
La ré-entrée en position est une politique explicite : `hold` (jamais
d'ajout) ou `topup` (tranche séparée avec son propre bracket SL/TP ; un EXIT
stratégie ferme toutes les tranches).

## Découverte structurelle

Dans le duo, AK 2 % + UTBot 0,75 % = 2,75 % < cap 3 % : il n'y a JAMAIS de
contention inter-stratégies. Les 31 refus `risk_cap_symbol` de la baseline
(19 AK + 12 UTBot) étaient TOUS des ré-entrées en position (auto-collisions) —
vérifié par le run `duo_arb_hold` : 46 `reentry_hold` (19 AK + 27 UTBot,
dont 15 ex-`hold` broker) et **fills identiques bit-à-bit à la baseline**
(404 fills, delta equity 0,00 $). L'enchère sans pyramidage dégénère
exactement en guichet quand il n'y a pas de contention : parité prouvée.
La « règle de ré-entrée implicite jamais décidée » est donc LE seul vrai
paramètre du duo ; l'enchère inter-stratégies ne mordra que sur des configs
à ≥ 3 manches par symbole (ex. trio avec HA) ou multi-symboles.

## Chiffres (conventions legacy, fill au close ≈ +10 pts d'optimisme)

| Run | Equity finale | Net | DD max | Net AK* | Net UTBot* | Trades |
|---|---|---|---|---|---|---|
| `duo_ak_utbot` (baseline) | 44 101,55 $ | +341,02 % | 32,13 % | 11 921,69 $ | 22 179,87 $ | 202 |
| `duo_arb_hold` | 44 101,55 $ | +341,02 % | 32,13 % | identique | identique | 202 |
| `duo_arb_topup` | 49 090,86 $ | +390,91 % | 34,35 % | 13 823,53 $ | 25 267,33 $ | 247 |
| `duo_arb_topup_f25` (plancher 25 %) | 47 837,40 $ | +378,37 % | 35,17 % | 13 593,79 $ | 24 243,61 $ | 229 |

\* convention fills : Σ realized − frais d'entrée, tranches repliées sur la
stratégie de base ; la somme colle au gain d'equity au cent près.

Topup : +4 989,31 $ (+49,89 pts) pour +2,22 pts de DD ; ratio net/DD 11,4
contre 10,6 baseline. Entrées : AK 53 (33 + 20 top-ups), UTBot 194
(168 + 26 top-ups) ; enchère : 211 pleines, 36 top-ups partiels
(fraction min 0,0006 / méd 0,146 / max 0,99), 1 `thesis_already_funded`.

## Stress d'exécution réaliste (reprice 15m, entrée open suivant + slippage)

| Slippage | Baseline duo | Arbitre topup | Δ points |
|---|---|---|---|
| 0 bps | +327,37 % (DD 32,6) | +380,75 % (DD 34,8) | +53,4 |
| 2 bps | +284,21 % (DD 34,3) | +327,04 % (DD 36,6) | +42,8 |
| 5 bps | +227,47 % (DD 36,9) | +257,45 % (DD 39,3) | +30,0 |
| 10 bps | +150,80 % (DD 40,9) | +165,63 % (DD 43,5) | +14,8 |

Intégrité : 0 trade skippé, repricing propre aux 4 niveaux. L'avantage
persiste partout mais fond avec le slippage (les top-ups d'UTBot y sont les
plus sensibles, cohérent avec sa fragilité connue).

## Décisions (mise à jour 2026-07-17)

1. **Règle de ré-entrée — TRANCHÉE par l'utilisateur : `topup`, harnais
   seulement.** Le pyramidage borné par le budget de thèse est la politique
   cible validée (mérite UTBot > AK, plancher 0), mais RIEN n'est porté en
   prod : la prod reste sur le guichet actuel (pas de pyramidage), l'arbitre
   reste un mode de replay. Toute promotion en prod resterait une décision
   explicite ultérieure avec commit dédié.
2. **Plancher de top-up** : les tranches poussière existent (min 0,40 $ de
   risque) mais ne coûtent rien en simulation : le plancher 0,25
   (`duo_arb_topup_f25`) fait MOINS bien que le topup pur sur rendement
   (−12,5 pts) ET sur DD (+0,8 pt) — il coupe surtout les top-ups d'AK
   (20 → 1). Recommandation : plancher 0 en replay ; en prod réelle un
   plancher purement opérationnel (minimum notionnel d'exchange) suffirait.
3. **Mérite v1** : rang statique (UTBot cœur > AK satellite acté). Un mérite
   appris (expectancy glissante ex ante) est possible en v2.
4. **Promotion prod** : nécessite de porter l'enchère dans
   `orum/portfolio/paper_engine.py` (+ tranches broker si topup) — décision
   explicite séparée, non incluse ici.

## Empreintes

- data : `d5efff8d2d9461ad1f1ab189b01b0bdfa65cf3d3f1e72a36e3ebdcc3a4893571`
- config baseline : `b6d36729d9509c229f43f6b93829e5f4ff9e9457e15d79597d0d2b765dea742f`
- config duo_arb_hold : `92af85749a2fa277fee115d32b4afdcc98b6c686be004336cbd8c801ba8bba8e`
- config duo_arb_topup : `06049892cfddbbfbfc46150bdb41c942a6a62fdaf850ed09c6d3e95c09b13cca`

Artefacts : `backtests/runs/duo_arb_{hold,topup,topup_f25}/`
(`arbiter_report.json`, `events.jsonl` avec événements `auction`,
`reprice.json` pour topup).
