# R2 — Résultats de la phase de développement (2026-07-26)

**VERDICT : ABANDON** (critères 2 et 5 du plan gelé `DEV_PLAN.md`, commit `28f0b04`). Conformément au protocole, **les données de confirmation Kraken 2024-2026 n'ont jamais été analysées** et ne le seront pas.

## Ce qui a été testé

270 cellules de grille (fenêtres de rendement × seuils × volume × contraction OI) sur Binance BTCUSDT+ETHUSDT 2020-09→2023-12, entrée au premier rebond 1 min, horizon 2 h, coûts réalistes (taker 5 bp + pire prix de la bougie d'entrée + spread croissant avec la vol), épisodes clusterisés BTC+ETH et cooldown 12 h. Sortie brute : `screening_results.json`.

## Pourquoi l'abandon

| Critère gelé | Meilleure cellule (sur 270, sélection assumée) | Déclenché |
|---|---|---|
| 1. N ≥ 25 épisodes | 59 clusters | non |
| 2. Espérance nette 2 h > 0 | **−2,9 bp** | **OUI** |
| 3. Δ (contribution OI) ≥ 10 bp | +97,1 bp apparents | non |
| 4. Δ non concentré | ok | non |
| 5. Δ stable entre 2020-21 et 2022-23 | **+153,4 → −109,0** (inversion de signe) | **OUI** |

Lecture d'ensemble de la grille — plus parlante que la meilleure cellule :

- **L'espérance nette à 2 h est négative sur la quasi-totalité des cellules** (typiquement −20 à −70 bp). Le pattern de base « acheter le premier rebond après une chute extrême » ne paie pas ses coûts sur 2020-2023, même sur la venue la moins chère (Binance 5 bp taker).
- Les Δ élevés s'accompagnent systématiquement de **31-47 épisodes non appariables** sur ~50-90, et de signes instables entre sous-périodes : c'est la signature du bruit sur petit échantillon, pas d'un effet.
- Autrement dit : les deux prédictions hostiles de l'itération 2 de GPT-5.6 se vérifient empiriquement — (a) la prime d'immédiateté est captée avant l'entrée taker tardive, les coûts endogènes mangent le reste ; (b) l'originalité de R2 reposait entièrement sur l'incrément OI, qui se révèle être du bruit.

## Limites de cette phase (assumées)

C'est un screening de développement, avec des approximations déclarées (normalisations par médianes journalières, fill au pire prix de bougie, modèle de spread simple, OI 5 min laggé). Ces choix étaient volontairement **défavorables à la stratégie sur les coûts et favorables sur la sélection** (meilleure cellule de 270). Qu'elle échoue dans ces conditions — sélection maximale, coûts réalistes — est une réfutation forte, pas un artefact.

## Conséquence pour la mission edge-research

R1 (turn-of-month) et R2 (post-deleveraging) sont tous deux rejetés empiriquement. Conclusion officielle de la mission : **aucun edge suffisamment crédible identifié dans cette itération** — voir `/Applications/0rum/Docs/EDGE_RESEARCH_2026-07.md` §8.

Actif conservé malgré tout : l'infrastructure (archives Binance dev 2020-2023, collecte Kraken 2024-2026 vierge et continue, scripts de screening pré-enregistrés) est réutilisable pour une campagne 2 sous contraintes durcies.
