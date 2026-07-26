# Audit Phase-0 — London Strategic Edge (api.londonstrategicedge.com) — 2026-07-26

Vendor gratuit (levée £1,5M nov. 2025, 35k inscrits, SDK open-source `lse-data`). Audit empirique exécuté en session avec clé utilisateur (stockée `~/.config/0rum/lse.key`, jamais committée). Quotas mesurés : 200 appels/min, 5 000 lignes/appel, 5 exports/h, 16 Go/sem, 50 Go/mois.

## Verdict global : **PASS pour stocks, options, FX, crypto (post-2020)** — avec caveats délimités

## Vérifications empiriques

| Test | Résultat | Verdict |
|---|---|---|
| BTC/USD daily vs Coin Metrics 2020-2023 | corr. rendements 0,9935 ; écart médian 0,11 % ; max 3,15 % | PASS |
| Trous crypto | **62 jours manquants fév-avr 2020** (crash COVID absent !) + 1 jour 2023 ; complet après | PASS avec exclusion pré-2020-05 |
| Profondeur stocks | AAPL/SPY depuis 2003-09-10 ; AAPL 2015 = 253 jours complets | PASS (conforme) |
| Ajustement splits | AAPL autour du 4:1 (2020-08-31) : prix rétro-ajustés correctement | PASS ; ajustement dividendes non documenté (feeds séparés fournis) |
| Intraday stocks | 1 min AAPL 2016 : 745 barres/jour = pré/post-market inclus | PASS (filtrage de session requis) |
| FX | EUR/USD daily depuis 2005 (mieux que le « 2009 » annoncé) | PASS |
| Macro | cpi_yoy depuis 1914-12 | PASS descriptif ; **AUCUN vintage point-in-time → interdit comme variable de signal event-based** (règle C2) |
| Chaîne d'options actuelle | IV + greeks complets, plage IV plausible (0,12–1,57) | PASS ; **IV/greeks = modèles maison LSE** → pour la recherche, recalculer l'IV depuis les prints |
| **Profondeur options** | Export `dataset=options` SPY 2015-06-01→05 : **243 254 prints, 2 Mo parquet**, colonnes {ts ms, underlying, expiry, type, strike, price, size, exchange, conditions, osi} ; sha256 fourni par l'API | **PASS — revendication « prints depuis 2014-06 » vérifiée** |
| Endpoint synchrone options | `option_candles` ne sert que les contrats récents (0 ligne dès l'expiration 2025-12) ; la profondeur passe par les jobs d'export | Architecture comprise, pas un défaut |
| Classes récentes | ETF, commodities, indices, volatilité, taux : premières données **avril-juillet 2026** — quasi zéro historique malgré le marketing | FAIL sur ces classes |

## Caveats de design (à intégrer à toute recherche sur ces données)

1. **Options** : utiliser les prints bruts (vérité de marché) et construire notre propre surface IV — ne pas dépendre des greeks maison. Codes exchange/conditions à décoder (nomenclature OPRA).
2. **Crypto** : nos propres archives Binance/Kraken restent la référence primaire ; LSE en recoupement.
3. **Macro** : contexte descriptif uniquement, jamais variable décisionnelle (pas de vintages).
4. **Pérennité** : gratuit financé par VC → tout dataset devenant load-bearing est archivé localement avec manifeste sha256 (les artefacts d'export expirent en 48 h).
5. **ToS** : page en rendu JS, non auditée — restrictions d'usage à vérifier par l'utilisateur.
6. Exports parquet/arrow uniquement ; `est_bytes` à la soumission est une estimation pré-filtrage (11 Go annoncés → 2 Mo réels) — juger sur `bytes` du job ready avant téléchargement.

## Conséquence stratégique

La tape d'options US 2014→2026 (3 186 sous-jacents) gratuite **ouvre la famille volatilité/dérivés** — normalement verrouillée par le coût des données (ORATS et al.). C'est le candidat naturel du périmètre d'une campagne 3 : signaux de surface de vol (variance risk premium, skew, term structure) sur sous-jacents liquides, nombreuses décisions indépendantes par an, tradables via options US IBKR (non concernées par PRIIPs — à confirmer selon les permissions du compte). **Le budget data de 50-100 €/mois reste intact** — aucune dépense nécessaire à ce stade.
