# Chantier 4b — restauration ATR et levier notionnel 3x

Date : 2026-07-17. Décision opérateur : conserver le topup direct en paper,
restaurer le dimensionnement, les caps et R sur `2×ATR`, et laisser le plafond
notionnel à 3x.

Les signaux AK MACD et UT Bot, leurs stops, leurs paramètres, le mérite UT Bot
avant AK, le cap BTC 3 %, le cap portefeuille 5 % et les tranches indépendantes
ne changent pas. `risk_distance` reste enregistré pour audit/affichage mais ne
réduit plus la taille.

## Replay de confirmation

Période 2024-01-01 → 2026-07-15, 88 897 cycles, snapshot identique
`d5efff8d2d9461ad1f1ab189b01b0bdfa65cf3d3f1e72a36e3ebdcc3a4893571`,
configuration `backtests/configs/duo_ak_utbot.yaml` avec `max_leverage: 3.0`.

| Variante | Équité finale | Rendement net | DD max |
|---|---:|---:|---:|
| hold restauré | 44 101,55 $ | +341,02 % | 32,13 % |
| topup restauré | 49 090,86 $ | +390,91 % | 34,35 % |
| delta topup | +4 989,31 $ | +49,89 pts | +2,22 pts |

Les résultats reproduisent exactement les résultats historiques du chantier 1.

## Empreintes

| Fichier | SHA-256 |
|---|---|
| hold `fills.jsonl` | `904a568256a31093f0e2f59d1c2dfd19690db5d9281c30ac689cbdd2afae1c44` |
| hold `equity.jsonl` | `9fd3491d8a5635e71f4911ac13c34d7119ef6e1b0d233a2ba60c1084b8b24c7d` |
| topup `fills.jsonl` | `67e048a762b9b976b463788f3b7a7925317772b52142b246087c58354a932e99` |
| topup `equity.jsonl` | `3d660b4e7cfdf596411b542def7d1554f44b5220304cd6b38e268e6e073285ba` |

## Limite assumée

Le `risk_pct` représente de nouveau un budget ATR, pas la perte maximale au
stop. Un stop plus distant que `2×ATR` peut donc produire une perte supérieure
au pourcentage affiché, même lorsque le plafond notionnel 3x n'est pas atteint.
Cette convention est explicite et demandée pour le runtime paper.
