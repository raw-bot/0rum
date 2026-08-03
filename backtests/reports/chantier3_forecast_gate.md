# Chantier 3 — Étude offline bornée du forecast gate : verdict

Date : 2026-07-17. Module `scripts/replay_harness/gate_study.py` + CLI
`gate-study`, run `backtests/runs/gate_study_v1/` (`gate_predictions.jsonl` :
3 700 origines, `gate_study.json`). Tests : `tests/test_gate_study.py` (11).
Empreintes : data BTC 1h `a8afc64cfe367b4a…`, config `bbe9b0f3bdff56e0…`.

## Question et protocole

Le gate est débranché (verrouillé 310/311 sur 2,5 ans). Question unique : ses
variables — (trend, vol) sur 24 barres 1h alimentant des quantiles
conditionnels KNN (80 voisins) des rendements forward 6/12/24 h —
prédisent-elles quoi que ce soit hors échantillon ? Les seuils de
qualification n'ont PAS été touchés (interdit). Grille d'origines toutes les
6 h sur 2024-01 → 2026-07 (3 700 origines, fenêtre de 900 barres identique à
la prod, évaluateur `fast_gate` à parité de décision testée), contrôle
climatologique = quantiles inconditionnels des MÊMES cibles d'entraînement.
**Règle de verdict pré-enregistrée dans la docstring du module avant tout
résultat** : contenu prédictif ssi, à h24, skill pinball vs climatologie > 0
ET direction non chevauchante > max(0,5 ; base climatologique) + 2 erreurs
types.

## Résultats (3 700 origines, 2,5 ans)

| Horizon | Skill pinball vs climatologie | Direction modèle | Direction climato | Base « up » | Couverture p10-p90 (cible 0,80) | Spearman p50↔réalisé |
|---|---|---|---|---|---|---|
| 6 h | **−0,014** | 0,506 | 0,505 | 0,520 | 0,760 | 0,011 |
| 12 h | **−0,036** | 0,501 | 0,495 | 0,516 | 0,736 | 0,028 |
| 24 h | **−0,073** | 0,508 | 0,502 | 0,517 | 0,712 | 0,020 |

Sous-échantillon non chevauchant h24 (n=925) : direction 0,531 contre un
seuil requis de 0,533 — raté — et le skill est négatif partout. Déciles de
p50 prédits : aucun ordonnancement des rendements réalisés (bruit pur, aucune
monotonie). Couverture 71-76 % pour une cible de 80 % : les quantiles KNN
sont TROP ÉTROITS (surconfiance) — pire que la climatologie sur leur propre
métrique de calibration.

## Verdict (règle pré-enregistrée, appliquée telle quelle)

`predictive: false` — les deux critères échouent.

1. Le KNN conditionnel est PIRE que les quantiles inconditionnels de sa
   propre fenêtre d'entraînement, à tous les horizons. Le conditionnement
   (trend, vol) n'apporte rien : il retire de l'information.
2. La direction est indiscernable de la pièce et INFÉRIEURE au simple taux de
   base haussier de la période (0,52).
3. Le verrou de qualification faisait donc exactement son travail en gardant
   ce modèle hors des décisions (actif 0,7 % du temps, qualifié h24 2,5 %).

**RECOMMANDATION : suppression complète du code du forecast gate.**

## SUPPRESSION EXÉCUTÉE (2026-07-17, sur décision explicite de l'utilisateur)

Supprimés : `orum/portfolio/forecast_gate.py`, `orum/portfolio/forecast_history.py`,
le bloc forecast de `orum/portfolio/paper_engine.py` (constructeur + refresh +
influence + persistance), les constantes `FORECAST_*` de `orum/paths.py`, la
couche calibrée du dashboard (`dashboard.py` + fan calibré/historique dans
`static/dashboard.js`/`.css`, versions d'assets bumpées v34/v35 — l'éventail
« scénarios » purement visuel est conservé), et côté harnais `fast_gate.py`,
`gate_study.py`, le câblage `--gate` de `runtime_replay.py`/`arbiter.py`/CLI,
plus les 33 tests forecast. Un bloc `forecast_gate:` résiduel en config est
ignoré (commentaire dans `paper_engine.py`).

Vérifications : suite complète **841 tests verts** (en 6 s — les tests de
parité fast-gate étaient les plus lents) ; constructeur smoke sur
`state/portfolio.yaml` réel (5 stratégies, clé résiduelle ignorée) ; cycle
live du worker post-édition sain (equity 14:55:04 UTC normale) ; run de
reproduction `duo_post_removal` 2024→2026 comparé fills bit-à-bit à
`duo_ak_utbot` (résultat consigné dans `duo_post_removal/arbiter_report.json`).
Les artefacts d'étude (`gate_study_v1/`, runs de référence avec champ `gate`)
restent valables : le gate n'a jamais influencé une décision.
Le dashboard live (process long-vivant) continue sur l'ancien code jusqu'à
son prochain redémarrage — décision utilisateur — et bootera proprement.

## Chantier 4 (promotion de fast_gate) — caduc

La promotion de `fast_gate` en prod n'a de sens que si le gate a un avenir.
Il n'en a pas : chantier 4 sans objet. `fast_gate` garde sa valeur comme
OUTIL DE HARNAIS (c'est lui qui a rendu les replays multi-années possibles)
tant que la suppression n'est pas actée.
