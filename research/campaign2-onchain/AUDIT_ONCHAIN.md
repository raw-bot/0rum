# Campagne 2 — Phase 0 : Audit des sources on-chain (2026-07-26)

Verdicts par source, sondes exécutées en session (endpoints, profondeur, fraîcheur vérifiés).

## PASS — utilisables pour la génération d'hypothèses

| Source | Variables | Profondeur | Cadence | Caveats |
|---|---|---|---|---|
| **DefiLlama stablecoins** (`stablecoins.llama.fi`, gratuit sans clé) | Offre circulante totale par peg (USD/EUR/…), par stablecoin, par chaîne | **2017-11 →** (3 162 points quotidiens vérifiés) | 1j | Changements de méthodologie possibles (traitement des bridges/double comptage) ; à figer par snapshot |
| **Coin Metrics Community** (`community-api.coinmetrics.io`, gratuit) | PriceUSD, **CapMVRVCur** (vérifié : 2010-07 → 2026-07-25, 5 852 points, une requête), AdrActCnt, TxCnt, HashRate, SplyCur, IssTotUSD, **FlowInExNtv / FlowOutExNtv / FlowInExUSD** | 2010-2011 → | 1j | **Flux d'exchange en statut `flash`** = valeurs provisoires révisables → tout signal doit être laggé ≥ 2 j et la version finalisée utilisée en backtest ; couverture = liste d'exchanges supportés par CM, composition variable dans le temps (risque de rupture structurelle) |
| **DefiLlama TVL** (`api.llama.fi`) | TVL agrégée et par chaîne | 2017-09 → | 1j | TVL libellée USD → endogène au prix des tokens ; utiliser des versions dénominées en natif |
| blockchain.info charts | Variables réseau BTC (secondaire, recoupement) | 2010 → | 1j (par requêtes chunkées) | Décimation au timespan=all ; source de contrôle uniquement |
| mempool.space | Mempool, frais BTC | ~3 ans | ~h | Trop court pour les signaux lents ; features de régime récent seulement |

## FAIL / exclus

- Métriques premium CM : TxTfrValAdjUSD, CapRealUSD, SplyActPct1yr, cohortes LTH/STH → **interdites** (toute hypothèse en dépendant est interdite).
- Glassnode (SOPR, HODL waves…), CryptoQuant : payants → exclus.
- Depegs de stablecoins comme événements : ~un événement significatif/an → viole la contrainte 1 de la charte.

## Conséquences de design (gelées pour la Phase 1)

1. Flux d'exchange : lag minimal de 2 jours (statut flash) + test de sensibilité à la composition de couverture.
2. Toute variable USD-libellée transformée en version relative (÷ mcap, ÷ supply) ou native.
3. Nulls obligatoires par hypothèse : momentum prix + vol (les variables on-chain sont corrélées aux rendements passés — la causalité inversée est le piège n°1 du domaine).
4. Horizon hebdo/mensuel, rebalancement avec hystérésis (contrainte edge ≥ 5× coûts).
