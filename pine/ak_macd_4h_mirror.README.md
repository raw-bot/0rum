# 0rum Mirror — AK MACD 4h long-only

Miroir d'AUDIT du cerveau live (`orum/external/ak_macd.py`, machine à candidats +
overrides goal.yaml du 2026-07-01 : 4h, allow_short=false). Le bot ne lit PAS TV —
ce script sert uniquement à vérifier visuellement (et via MCP `data_get_pine_labels`)
que le chart raconte la même histoire que le Python.

## Parité vérifiée (2026-07-06)

Protocole : `confirmed_entries()` du labo (mêmes fonctions que le live) sur BTCUSDT 4h
Binance, 2 999 barres closes (2025-02-21 → 2026-07-06), vs labels BUY du miroir sur le
chart TV (Pine v6, compilé sans erreur).

- Python : **35 entrées LONG confirmées** sur la fenêtre.
- Pine : **35 labels BUY** sur la même fenêtre (52 au total, les 17 antérieurs sont
  hors fenêtre de comparaison).
- Les 15 dernières comparées timestamp par timestamp : **15/15 identiques**
  (2025-12-09 16:00 → 2026-06-22 08:00 UTC).

Résidu connu et accepté : l'amorçage EMA diffère (TA-Lib SMA-seed côté Python,
récursion pure côté Pine) → seuls les tout premiers signaux de l'historique Pine
peuvent diverger ; ils sont hors fenêtre. La parité sur ≥16 mois récents est exacte.

## Rappels

- Signaux valides à la CLÔTURE de barre uniquement (la barre en formation peut
  afficher/retirer un flip en intrabar — comportement normal, le bot ne regarde
  que les barres closes).
- Ne PAS dériver les inputs du miroir : les défauts = AkMacdParams du bot. Si le
  bot change (goal.yaml/strategy.yaml), re-porter puis re-tester la parité.
- Test de parité reproductible : `.sandbox/0rum-one-shot-home/0rum-trading/scripts/parity_4h_mirror.py` —
  `cd .sandbox/0rum-one-shot-home/0rum-trading && uv run python scripts/parity_4h_mirror.py 3000`.

## État des slots TV après nettoyage (2026-07-06)

Chart : UN seul indicateur — « 0rum Mirror — AK MACD 4h long-only » (BTCUSDT 4h).
Slots sauvegardés (les slots ne comptent PAS dans la limite d'indicateurs du plan,
seuls les indicateurs SUR le chart comptent) :
- slot « AK MACD 15m » → contient LE MIROIR 4h (v4) — actif.
- slot « AK MACD 15 » → contient l'OTE helper (n°19) — reliquat, supprimable à la main.
- slot « FPU connectivity test » → contient l'ancien miroir 15m (v20) — reliquat,
  supprimable à la main (la source de vérité est `pine/ak_macd_15m.pine` dans le repo).
Le MCP ne peut pas supprimer/renommer des slots — ménage manuel via Pine Editor →
menu Sauvegardés si souhaité.
