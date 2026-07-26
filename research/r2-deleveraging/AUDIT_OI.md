# R2 — Audit d'intégrité Open Interest (gate n°1) — 2026-07-26

Protocole : `/Applications/0rum/Docs/EDGE_RESEARCH_2026-07.md` §6. Critère : si l'intégrité de l'OI ne peut être établie sur au moins une venue d'exécution accessible (France, MiCA/MiFID), R2 est non testable → rejet opérationnel.

## Verdict : **GATE PASSÉ** — venue de confirmation : Kraken Futures

| Venue | OI historique | Granularité | Début | Verdict |
|---|---|---|---|---|
| **Kraken Futures** (PF_XBTUSD) | `api/charts/v1/analytics/{sym}/open-interest` | 300 s (aussi 3600 s, 86400 s) | **~2023-03** | **PASS** |
| OKX (BTC-USDT-SWAP) | rubik `open-interest-history` | 1D seulement en profondeur ; 5m/1H limités aux ~30 derniers jours | 2024-01 (1D) | **FAIL historique** (OK prospectif) |
| Binance (BTCUSDT) | Vision `futures/um/daily/metrics` | 5 min | **2020-09-01** | PASS (développement uniquement) |

## Contrôles d'intégrité Kraken (échantillon incluant le crash du 2024-08-05)

- **Cadence** : 289 points/24 h, intervalle exactement 300 s, zéro trou.
- **Valeurs** : aucun zéro, aucun NaN.
- **Sémantique des colonnes** : OHLC de l'OI vérifié — high ≥ max(open,close) : 100 % ; low ≤ min : 100 % ; continuité open(t)=close(t−1) : 100 %.
- **Unités** : BTC (≈ 2 100 BTC d'OI, ~2,5 % de la taille de Binance).
- **Stabilité** : re-téléchargement du même intervalle → octets identiques (pas de révision détectée en session ; la non-révision long terme sera surveillée pendant la collecte prospective).
- **Cohérence cross-venue (2024-08-05)** : corrélation de niveau OI Kraken/Binance 0,78 ; pendant la cascade 00h–08h UTC, deleveraging simultané des deux venues (−231 BTC Kraken / −10 491 BTC Binance, ≈ −11 % chacune).
- **Point d'attention** : corrélation des ΔOI 5 min entre venues = 0,08 seulement — le bruit intra-venue domine hors événement. Conséquence intégrée au design : les seuils de détection seront **normalisés par venue** (percentiles roulants), jamais absolus, et le transfert Binance→Kraken n'est supposé valide qu'à l'échelle des épisodes, pas barre par barre.
- **Prix 1 min** : `api/charts/v1/trade/PF_XBTUSD/1m` disponible sur toute la profondeur testée (2023-08 vérifié). Réserve : API « charts » non documentée officiellement (celle du front Kraken) — risque opérationnel de rupture, mitigé par la collecte prospective locale dès maintenant.
- **Funding Kraken** : historique v4 limité à ~1 an (2025-07→) — insuffisant. La variable de régime funding sera sourcée des archives Binance Vision (proxy de marché global, pas donnée d'exécution) — documenté comme substitution.

## Design des jeux de données (séparation venue + temps)

| Rôle | Venue | Période | Usage |
|---|---|---|---|
| **Développement** (déclaré entraînement, coût de sélection assumé) | Binance (metrics 5 min + klines 1 m + funding) | 2020-09 → 2023-12 | screening, choix des seuils, gel |
| **Confirmation** (intacte jusqu'au gel) | Kraken (OI 5 min + 1 m) | 2024-01 → 2026-07 | test confirmatoire unique |
| Prospectif | Kraken + OKX temps réel | 2026-07 → | audit opérationnel / paper |

Engagement : les données Kraken sont collectées dès maintenant mais **aucune analyse n'y sera exécutée avant le gel des paramètres** de développement (PREREG_CONFIRM.md à committer avant le run confirmatoire).
