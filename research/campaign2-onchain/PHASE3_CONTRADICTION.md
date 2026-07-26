# Campagne 2 — Phase 3 : contradiction externe, itération 1 adjugée (2026-07-26)

Verdicts GPT-5.6 : C1 défendable sous conditions, C2 défendable-proche-du-rejet, C3 rejetée.

## Adjudication

### C3 (activité réseau relative BTC/ETH) — objections ACCEPTÉES, candidate ÉLIMINÉE
Les trois objections fatales sont valides et non réparables dans le périmètre : (1) AdrActCnt/TxCnt ne mesurent pas la même grandeur économique entre chaînes ; (2) la migration L2 rend la série ETH L1 non représentative de l'écosystème (le signal vendrait ETH pendant une adoption réussie des rollups) ; (3) une seule paire = une seule expérience, pas une loi cross-sectionnelle. J'avais signalé ordinals et la paire unique ; GPT démontre que c'est structurel, pas paramétrique. Éliminée sans itération.

### C2 (pics d'inflows exchange) — ÉLIMINÉE AVANT BACKTEST, et leçon d'audit
L'objection 4 est fatale et représente **un échec de mon audit Phase 0** : j'avais attrapé le statut `flash` (révision des valeurs) mais pas la **rétroactivité des labels** — CM ajoute des exchanges (ex. Coinbase, annoncé 02/2025) et recalcule tout l'historique. La série téléchargée aujourd'hui n'est pas celle qui aurait été observable à chaque date passée, et aucun historique point-in-time des versions n'est disponible en tier gratuit. Per charte (« pas de révision rétroactive silencieuse ») : candidate tuée avant calcul. Les objections 3 (le lag de 2 j consomme probablement l'horizon causal) et 6 (flux visibles en temps réel par le marché → la revendication résiduelle « lenteur de digestion après J+2 » est étroite) achèvent le dossier. **Leçon consignée : auditer la stabilité des *labels/composition*, pas seulement celle des valeurs.**

### C1 (stablecoins) — conditions ACCEPTÉES, candidate RÉVISÉE
Les 6 conditions de GPT sont valides et intégrées :

1. Revendication dégradée : le mint net n'est PAS « du fiat frais destiné à BTC/ETH » — c'est un **signal de régime de liquidité de l'écosystème**, dont le pouvoir prédictif est une hypothèse empirique, pas une identité comptable.
2. Dénominateur sans prix : variable primaire = **Δ30j de log(offre agrégée des stablecoins USD fiat-backed)** — croissance de l'offre contre elle-même, aucun mcap crypto. Le ratio à la mcap devient descriptif.
3. Séparation par mécanisme d'émission : agrégat **fiat-backed uniquement** (139 actifs classés par DefiLlama, USDT+USDC ≈ 84 % du total vérifié en session) ; crypto-backed (DAI & co, endogènes au levier) et algorithmiques (UST !) exclus de la variable primaire.
4. Anti-concentration : sous-périodes + leave-out des épisodes extrêmes d'émission.
5. Null élargi pré-enregistré, unique : régression du rendement forward sur momentum prix 30/90 j + vol réalisée 30 j ; le signal doit apporter une valeur incrémentale à ce null composite, pas seulement au momentum 30 j.
6. Confirmation tardive : développement 2017-11→2022-06 (inclut le bull 2020-21 ET l'effondrement Terra) ; **confirmation 2022-07→2026-07** — 4 ans postérieurs à la popularisation du mécanisme et au régime « stablecoins = paiement/épargne ».

Objection 5 (peu d'expériences indépendantes malgré ~450 semaines) : acceptée — inférence par blocs de régime, et le go/no-go exigera une signification économique (avantage net ≥ 1 %/an à 20 k€ vs benchmark à exposition égale, comme R1), pas un p-value sur semaines chevauchantes.

## Statut

C1 révisée transmise pour itération 2/3 (spec-level review). C2/C3 concédées, non re-soumises. Si l'itération 2 ne produit pas d'objection nouvelle substantielle : pré-enregistrement committé puis test empirique (pipeline campagne 1).
