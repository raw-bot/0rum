# Synthèse finale & plan de backtests — phase recherche close

_Date : 2026-07-04. Sources : `oxfordstrat_synthese.md` (26 études), `tradingview_indicateurs_recherche.md`,
`tv_catalogue.md` (17 vidéos analysées). Prochaine phase : code._

## Fonction objectif (recadrée par cube, 2026-07-04)

Le bot n'est PAS en compétition avec le buy & hold (le stack BTC de cube hold par
ailleurs). Sa mission : **quand la période est propice, générer du rendement additionnel,
ludique mais net de frais**. Conséquences :
- Métriques d'évaluation : **PF/espérance conditionnels aux fenêtres actives**, temps
  d'exposition, R par fenêtre. "CAGR vs B&H" n'est PAS un critère d'échec.
- Le screen 4 ans (tout perd en absolu, même always_long, avec la géométrie 15m) se lit
  ainsi : trader 24/7 sans condition est mort ; l'edge est dans le QUAND. Rester dehors
  est une position.
- La Phase 2 (COT, corrélations, continuation, saisonnalité) = construction des
  **détecteurs de période propice**. Question de recherche centrale : le cerveau 4h
  long-only, gaté par ces détecteurs, a-t-il une espérance positive nette pendant ses
  fenêtres actives ?
- Réserve documentée : en 15m, même le régime favorable restait sous PF 1 (exit lab
  per-régime 0.64-0.86) → le gate seul ne suffit pas, la géométrie des sorties compte
  aussi (d'où le 4h long-only actuel).

## Décisions structurantes (actées avec cube)

1. **Panier multi-asset** : BTC + or (PAXG/XAUUSD) + ETH. Une stratégie n'est retenue que si
   elle est profitable sur ≥2 actifs décorrélés **sans re-tuning par actif** (anti-overfit n°1).
   Suivi de décorrélation du panier : méthode Oxfordstrat (corr sur fenêtre 256 barres de
   log-returns 10 barres) — les corrélations sont DYNAMIQUES, à surveiller, pas à supposer.
2. **Walk-forward obligatoire** pour toute optimisation (leçon @bennnytrades : jamais
   d'in-sample plein). Optimiser sur fenêtre N, valider sur N+1, rouler.
3. **Coûts toujours inclus** : 0.1% RT crypto spot, spreads réalistes or/indices. Toute
   stratégie à petits gains fréquents est présumée morte jusqu'à preuve du contraire.
4. **Benchmarks obligatoires** : Donchian 20/55, Supertrend (10,3), buy & hold. Un candidat
   qui ne bat pas Donchian ne paie pas sa complexité.
5. **Datation stricte des données exogènes** (COT : date de PUBLICATION vendredi, pas date
   des données mardi).
6. Données : Binance (BTC/ETH/PAXG, aggTrades pour volume-par-prix), Dukascopy (XAUUSD 20 ans,
   indices CFD), CFTC (COT 1986→), Yahoo (DXY, NQ, ZB daily). MCP TradingView pour
   contre-valider les implémentations d'indicateurs barre par barre.

## Classement final des hypothèses (potentiel × coût de test)

| Rang | Hypothèse | Origine | Potentiel | Coût | Statut a priori |
|---|---|---|---|---|---|
| 1 | A/B sur AK MACD live : sans filtre volume · MACD élargi · TP 1.5R→trailing · veto WR-bar | Oxfordstrat | Élevé (bot live) | ~1j | Données contre le filtre volume et le TP fixe |
| 2 | Benchmarks + question timeframe (15m vs 1h/4h/daily, même cerveau) | Oxfordstrat + Boring Edge | Élevé (structurel) | ~1j | Trend simple daily bat presque tout sur BTC |
| 3 | Saisonnalité intraday BTC/or (vol & rendements par heure UTC) | Catalogue (transversal sessions) | Moyen (socle) | ~0.5j | Effets d'open documentés — à chiffrer sur NOS actifs |
| 4 | COT or : extrêmes Commercials (COT-index) + retail opposé → rendement 1-4 sem → gate hebdo | Cat. n°3 | Élevé (filtre exogène unique) | ~1j | Littérature réelle mitigée ; meilleur filtre candidat |
| 5 | Batch ORB (ablation complète) : fenêtre {M5, 2×15m, 9:30-9:45} × filtre {∅, FVG-displacement, ATR, RVOL} × confirmation {∅, engulfing} × exit {TP-mini, 3:1, trailing, EoD} — indices + BTC (ancres open US & UTC) | Cat. n°8/13/16 + Zarattini | Élevé | ~2-3j | La seule famille intraday avec preuve académique partielle. Axe engulfing ajouté suite 2e passe Oxfordstrat : engulfing seul = D → vérifier qu'il n'est pas du poids mort dans la First Candle Rule |
| 6 | Corrélations-régime : corr(XAU,DXY), corr(BTC,NQ), corr(NQ,ZB) sur rendements → gates | Cat. n°4/5 | Moyen | ~0.5j | Pratique pro standard, quasi gratuit |
| 7 | Z-score valorisation XAU/DXY aux extrêmes → filtre réversion | Cat. n°1 | Faible-moyen | ~0.5j | Contredit volatility clustering — à trancher par les données |
| 8 | Batch niveaux vs placebo : POC veille · VAH/VAL · PDH/PDL · close veille · VWAP session · STD-CBDR · POC swing-leg · order block k×ATR — fréquence/amplitude de réaction vs niveaux aléatoires | Cat. n°2/6/12/14/17 | Élevé si positif (couche niveau = trou identifié du bot) | ~2j | Aucun a priori fort ; protocole placebo indispensable |
| 9 | AVWAP ±2σ/3σ + sweep Asian (stratégie n°7 complète) | Cat. n°7 | Faible | ~1j | Famille MR intraday = D avec frais ; tester après rang 3 |
| 10 | Cerveau Livermore (pivots swing + bruit ATR) en shadow à côté d'AK MACD | Oxfordstrat | Élevé | ~3-5j | Meilleur profil du corpus coûts inclus (17.2% CAGR, −44% DD) |

Écartés définitivement : FVG-aimant, order/rejection/breaker blocks discrétionnaires, OTE/Fib,
CBDR-σ comme méthode (le costume gaussien est faux), confluence miroir, tout playbook à
suppression de zones a posteriori (cat. n°9/11/15, détails au catalogue).

## ⚠️ RÉCONCILIATION avec le labo existant (découvert le 2026-07-04, Phase 0)

Le repo (`.sandbox/0rum-one-shot-home/0rum-trading/scripts/`) contient déjà un labo complet
(donchian_strategy, exit_lab, experiment_entry_filters, experiment_ict_filters,
research_strategy_screen, backtest_4h_validation, runners 1m avec parité prouvée,
walk-forward, sanity buy&hold). Résultats déjà acquis (`state/*.txt`) :

1. **Le rang 1 (A/B AK MACD 15m) est DÉJÀ RÉPONDU — négatif.** Baseline 15m : PF 0.80,
   exp −0.064R. Retirer le filtre volume : PF 0.85 (confirme Oxfordstrat) mais toujours
   négatif. Exit lab (6 variantes dont trailing/structural) : TOUTES négatives (meilleure :
   structural PF 0.75). Aucun tweak ne sauve le 15m.
2. **Le screen 4 ans (2022-07→2026-06, B&H +205%) est la pièce maîtresse** : TOUTES les
   familles d'entrée — macd_akm, donchian, ema_cross, rsi_revert, bb_revert, fvg_ce et
   même **always_long** — finissent PF 0.52-0.59, 0 fold sur 8 en PF>1. Quand always_long
   perd (−0.365R/trade) pendant que le marché triple, le coupable n'est pas l'entrée :
   c'est **la géométrie bracket 15m (SL 1.5×ATR, RR 1.5) + frais** — validation frontale
   de la leçon Oxfordstrat n°1/n°9.
3. **Le bot est déjà passé en 4h long-only le 2026-07-01** (goal.yaml : "switched 15m->4h.
   Validated long-only edge") et tourne en paper. Le rang 2 (question timeframe) est donc
   partiellement acté ; la validation OOS 4h n'a pas de fichier de résultat sauvegardé →
   à re-runner et archiver (`backtest_4h_validation.py`).
4. Contexte marché du labo : BTC 105k→58k (−44%) sur 2025-01→2026-06 ; +205% sur 4 ans.
   Les deux régimes sont couverts, la conclusion 15m tient dans les deux.

**Priorités re-scopées** : rangs 1-2 consommés → la frontière est multi-asset (or/ETH),
COT-gate, corrélations-régime, batch ORB (BTC via Binance 1m ; ⚠️ indices intraday =
trou de données, yfinance ne donne que du daily — à résoudre si besoin), batch niveaux.

## Phase 0 — LIVRÉE (2026-07-04)

`scripts/data_layer.py` (+ caches `state/data_cache/`), smoke-testé :
- `fetch_klines(symbol, interval, n)` — Binance tout symbole (PAXG or, ETH, BTC…).
- `fetch_daily(ticker)` — yfinance daily : GC=F (or 2005→, 5404 j), DX-Y.NYB (DXY),
  NQ=F, ZB=F… (DX=F est mort sur Yahoo).
- `fetch_cot(years, prefix)` — CFTC legacy futures-only via httpx (urllib = SSL fail),
  match par PRÉFIXE (piège : "contains" attrapait MICRO GOLD), `usable_from` = mardi+3j
  (alignement date de publication). Or COMEX : 182 sem., comm_net −205k ✓. BTC CME : ok.
- `cot_index(values, 156)` — %-rank Briese 3 ans.

## Résultats Phases 1-2 (2026-07-05) — scripts + archives dans state/

1. **Validation 4h re-runnée et archivée** (`backtest_4h_validation_result.txt`) — le cerveau
   live est SAIN : long-only runner PF 1.61 full (2018→, 139 tr), **PF 2.02 OOS ≥2024**
   (52 tr, 57.7% win), 7 années positives sur 9 (seul 2021 vraiment négatif), ~18 tr/an.
   Profil déjà "période propice" (rare et sélectif).
2. **Benchmarks cross-asset daily** (`bench_cross_asset_result.txt`, 0.1% RT) :
   - BTC : Donchian 20/10 CAGR +39.3% (B&H +35.6%) avec DD 47% vs 83%, expo 40%.
   - ETH : Donchian ×48.9 (+55.9%) vs B&H ×5.9 (DD 41% vs 94%). Supertrend semblable.
   - Or GC : trend +5-7% CAGR (sous B&H +16.7% — l'or grinde), mais DD 18-23% et expo ~50%.
   → Le daily trend long-only crypto EST une machine à période propice (2 bugs harnais
   corrigés en route : bougie signal dans son propre canal ; CAGR sur équité échantillonnée
   aux trades).
3. **Étude COT or 20 ans** (`cot_gate_result.txt`) — signal réel mais SENS INVERSE de la
   vidéo : sur l'or, les extrêmes de positionnement CONTINUENT (comm ≤20 → +1.91%/20j vs
   baseline +0.82%, hit 67%, stable pré/post-2016 ; noncomm ≥80 pareil). Le setup short
   "divergence" de nolan.vader → rendements POSITIFS (+1.28%/20j) : le shorter aurait perdu.
   Confirme volatility clustering : trader AVEC l'extrême, pas contre.
4. **Gate appliqué** (`cot_gated_gold_result.txt`) — leçon en deux temps :
   - En FILTRE D'ENTRÉES breakout : ÉCHEC net (bloque les ré-entrées, 8-30 trades/21 ans,
     CAGR ≈ 0). Le COT n'est PAS un filtre d'entrée.
   - En FENÊTRE DE POSITION (long tant que comm ≤20) : ×2.01 sur 21 ans avec **15%
     d'exposition seulement**, 20 fenêtres, 65% win, maxDD 5.2% (⚠ DD mesuré aux clôtures
     de fenêtres, intra-fenêtre non capturé). Efficience par unité d'exposition ≈ 2× le
     buy & hold. gate_either : ×2.14 / 15.5% expo.
   → **Candidat module bot "or COT-window"** : quand comm COT-index ≤ 20 → long PAXG
   jusqu'à sortie de zone. ~1 signal/an, cadence hebdo, zéro stress — du beurre dans les
   épinards au sens strict. À valider : walk-forward des seuils (20/156w figés a priori
   ici — PAS optimisés, c'est déjà du quasi-OOS) et DD intra-fenêtre en daily.

5. **Validation finale pré-câblage** (`validation_finale_result.txt`, 2026-07-05) :
   - **Grille COT robuste** : les 12 cellules (LO 15-30 × lookback 104-208w) sont positives
     (×1.28 à ×2.30) — pas un pic isolé. Correction honnête : le DD INTRA-fenêtre réel du
     20/156 est **13.6%** (pas 5.2% — l'ancien chiffre n'échantillonnait qu'aux clôtures).
     Chiffres réalistes du module : ×2.0-2.3 sur 21 ans, DD 14-23%, expo 15-26%.
   - **Kill-criteria Donchian daily BTC** (64 tr, win 43.8%, avg win +3.39R / loss −0.74R,
     ×18.26 full-notional, maxDD quotidien 54.3%, pire série 6) : couper si série de
     pertes > 9, ou DD > 68% (échelle full-notional), ou un trade < −2.7R. NB : en sizing
     bot (2% risque/trade fixed-fractional), le DD équivalent est ~12-16% d'équité, pas 54%.
   - Corrélations mesurées (3 199 j) : BTC/ETH +0.79 (ETH = demi-diversifieur au mieux) ;
     **or/crypto +0.10-0.14** = la vraie jambe indépendante du panier.

**Architecture cible actée avec cube (2026-07-05)** : moteur crypto = BTC 4h AK (validé,
live) + Donchian daily BTC (2e logique, même actif) ; moteur or = fenêtre COT (+ tendance
daily éventuelle) ; ETH en option demi-taille après backtest 4h. Kill-criteria écrits
AVANT tout branchement.

## Exigences d'exploitation (cube, 2026-07-05) — bot 24/7, ne peut pas s'arrêter

- **Court terme (Mac)** : remplacer le nohup par **launchd** (KeepAlive = relance auto
  après crash/reboot) + `caffeinate` contre le sleep ; watchdog sur `state/heartbeat.json`
  (existe déjà) avec alerte si silence > N minutes.
- **Moyen terme (la vraie solution)** : le repo a un Dockerfile + docker-compose → déploiement
  sur un petit VPS (~5€/mois), indépendant du Mac, uptime 99.9%.
- **Sécurités crash** : kill-criteria chiffrés ci-dessus dans goal.yaml ; reprise d'état à
  froid (candle_history/events déjà persistés) ; avantage structurel des moteurs lents :
  une coupure de quelques heures est bénigne en daily/hebdo (contrairement au 15m).

6. **Simulation portefeuille + sizing par confiance** (`portfolio_sim_result.txt`,
   `scripts/portfolio_sim.py`, 2026-07-05) — l'idée de cube (levier dynamique sur seuil de
   confiance) est **VALIDÉE par les données** :
   - Score de confluence 0-3 à l'entrée (tendance SMA100 alignée + régime 90j aligné +
     impulsion >1.5×ATR / extrême COT profond), 366 trades poolés sur 3 moteurs :
     **parfaitement monotone** — score 0 : −0.118R (PERD) · 1 : +0.150R · 2 : +0.307R ·
     3 : +0.514R (PF 0.79 → 2.07). → levier dynamique autorisé ; mieux : les trades
     score 0 doivent être SAUTÉS (espérance négative), pas juste réduits.
   - Moteurs : donchian_btc PF 3.42 (+1.03R/tr) ✓ · **gold_cot PF 8.08** (+1.77R/tr, 65% win) ✓
     · **donchian_jpy PF 0.90 = MORT** (le FX ne trend plus, confirmation Pathfinder —
     la jambe USDJPY est rejetée par son propre backtest, le framework fait son travail).
   - Politiques de sizing (21 ans, équité partagée) : plat 2% → ×4.0 (+5.5%/an, DD 22%) ·
     modulé 1/2/4% → ×7.3 (+8.0%/an, DD 32%) · quart-Kelly/moteur → **×96 (+19.2%/an,
     DD 35%, pire année −28%)** — Kelly full-sample donc optimiste, mais la hiérarchie est
     claire : **la politique de taille vaut plus que n'importe quel tweak de signal**.
   - v2 à faire : ajouter le moteur BTC 4h AK live (export de trades depuis
     backtest_4h_validation), sauter les score 0, Kelly en expanding-window (sans lookahead).
   - **Jambe ETH tranchée par le test (relance de cube)** : donchian_eth PF 2.31
     (+0.66R/tr, 62 trades). Portefeuille AVEC vs SANS ETH : plat ×8.46 vs ×4.03 avec
     maxDD MEILLEUR (21.0% vs 22.5% — les trades ne se chevauchent pas parfaitement
     malgré corr 0.79) ; Kelly ×614 vs ×96 (+28.1%/an vs +19.2%), au prix d'une pire
     année dégradée (−35.9% vs −28.0%). → **ETH prend un siège plein** dans le panier ;
     réserve honnête : en hiver crypto, les jambes BTC+ETH draweront ensemble.

7. **portfolio_sim v2** (`portfolio_sim_v2_result.txt`, 2026-07-05) — panier final
   (ak4h_btc PF 1.61 intégré + donchian_btc 3.42 + donchian_eth 2.31 + gold_cot 8.08,
   jpy exclu), score toujours monotone (0: +0.01R → 3: +0.62R), politiques :
   - plat 2% : ×28.1, **+21.6%/an, maxDD 19.0%**, pire année −12.9%
   - modulé 1/2/4% : ×41.2, +24.4%/an, maxDD 23.2%
   - modulé + videur score-0 : ×34.9, +23.3%/an, pire année **−8.6%**
   - **Kelly expanding SANS lookahead : ×591, +45.4%/an, maxDD 47.7%** (pire année −38.2%)
   - Kelly expanding + videur : ×280, +39.5%/an, maxDD 38.8%, pire année −10.2%
   → le videur score-0 est un amortisseur de pire-année spectaculaire.
   ⚠ Limites : exits ak4h approximés à l'entrée pour l'ordre de composition ; équité
   échantillonnée aux trades ; overlap de risque non contraint (pas de cap d'expo global).

8. **Câblage PAPER livré** (`scripts/portfolio_shadow.py`, testé — upgradé de shadow à
   paper complet sur demande de cube) : poll horaire des 3 moteurs recherche
   (donchian_btc/eth + gold_cot), politique "modulé + videur", **P&L réel** : prix
   d'entrée/sortie avec frais 0.1% RT, R vs 2×ATR14, équité composée persistée
   (`portfolio_shadow_positions.json`), décisions+trades JSONL append-only
   (`portfolio_shadow.jsonl`), kill-criteria dans chaque signal. Zéro contact avec le
   worker/goal.yaml/producteur AK live (l'expérience 4h en cours reste intacte).
   - launchd : plist créé (`~/Library/LaunchAgents/com.0rum.portfolio-shadow.plist`,
     poll horaire + RunAtLoad), **chargement à faire par cube** :
     `launchctl load ~/Library/LaunchAgents/com.0rum.portfolio-shadow.plist`

**Politique active choisie par cube (2026-07-05)** : "secoué" = quart-Kelly, 6% de risque
par trade sur les 3 moteurs, score-0 inclus (capital = 10k d'argent de poche, drawdowns
assumés). Score loggé à chaque trade → la courbe "modulé+videur" reste reconstructible
a posteriori depuis le JSONL pour comparaison. Kill global ajouté : DD portefeuille > 60%
(sim : pire creux Kelly 47.7%) → coupe.

**Restent à faire** : observer le paper quelques semaines vs sim ; cap d'exposition
globale portefeuille ; passage éventuel via ExternalOrchestrator (source "local",
stratégies donchian_daily/gold_cot dans goal.yaml) ; VPS/Docker pour le vrai 24/7 ;
backtest 4h AK sur ETH/PAXG.

## Plan d'exécution (5 phases)

### Phase 0 — Socle (préalable à tout)
- [ ] Harnais de backtest commun (extension de `backtest_ak_macd.py` ou module dédié) :
      coûts paramétrés, walk-forward, rapports standardisés (CAGR, MaxDD, PF, win%, W/L, trades).
- [ ] Pipelines data : Dukascopy XAUUSD, Binance PAXG/ETH (compléter BTC), CFTC COT, DXY/NQ/ZB.

### Phase 1 — Quick wins & fondations (rangs 1-3) : ~2.5j
Livrables : AK MACD v2 candidate (si A/B positifs) · verdict timeframe · carte de saisonnalité.

### Phase 2 — Filtres de biais (rangs 4, 6, 7) : ~2j
Livrables : verdict COT-gate · verdicts corrélation-gates · verdict z-score valorisation.
Chaque filtre validé = greffable immédiatement sur le bot live (gate dans goal.yaml/strategy).

### Phase 3 — Batch ORB (rang 5) : ~3j
Livrable : la meilleure config ORB walk-forward sur 2+ actifs, ou son enterrement documenté.

### Phase 4 — Couche niveaux (rang 8) : ~2j
Livrable : verdict "les niveaux paient-ils ?" ; si oui, spec de la couche niveau du bot
(affinage entrées/sorties AK MACD, pas nouvelle stratégie).

### Phase 5 — Deuxième cerveau (rangs 9-10) : ~4-6j
Livrable : Livermore en shadow mode sur le panier, comparé à AK MACD v2 sur bougies live.

## Règles d'hygiène (léguées par la recherche elle-même)

- Tout RR affiché → vérifier d'où il vient (leçon n°15 : stop 0.047% = RR fictif).
- Toute convergence "plusieurs sources le disent" → vérifier l'indépendance (leçon n°14 :
  ferme de clones).
- Toute courbe lisse → chercher l'optimisation in-sample (leçon n°16).
- Tout indicateur TV → vérifier le repaint et recalculer hors TV (leçon Nadaraya-Watson).
- Win rate élevé ≠ edge (leçons RSI-2 et n°13) ; l'espérance nette de coûts est le seul juge.
