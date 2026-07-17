# Batch 2 — Frameworks & stratégies TradingView (soumission cube 2026-07-06)

_Grille 5Q habituelle + question spécifique à ce batch : **qu'apporte le candidat que notre
labo Python n'a pas déjà ?** (data_layer, walk-forward, coûts, benchmarks, portfolio_sim v2,
paper live). Verdicts : OUTIL / IDÉE / DOUBLON / POUBELLE._

## Le point structurel d'abord (il tranche la moitié du batch)

La thèse de la soumission — « la vraie mine d'or n'est pas l'indicateur, c'est le
framework » — est **correcte, et déjà consommée** : 0rum possède déjà ce framework, en
Python, et il est supérieur à tout framework Pine sur chaque axe qui compte :

| Axe | Frameworks Pine (HALDRO, Daveatt, Deeptest…) | Notre labo |
|---|---|---|
| Données | Historique limité par le plan TV (~20k barres deep backtest), pas d'aggTrades, pas de Dukascopy 20 ans | Binance/Dukascopy/CFTC/Yahoo, caches locaux |
| Walk-forward | Impossible de ré-optimiser par fenêtre en Pine (Deeptest ne fait que du découpage OOS des trades) | WF avec ré-optimisation, déjà câblé |
| Multi-asset | 1 chart = 1 actif (request.security plafonné à 40) | Panier BTC/ETH/or, équité partagée, Kelly |
| Coûts | commission_value déclaratif, pas de slippage réaliste | 0.1% RT + spreads, doctrine « présumé mort » |
| Protocoles | Rien (placebo, ferme de clones, IC…) | Placebo niveaux, kill-criteria, hygiène léguée |
| Sortie | Le résultat reste dans TV | Le backtest EST le code du bot |

Adopter un backtest-framework Pine serait un pas en **arrière** : c'est retourner dans le
strategy tester TradingView — précisément « les screenshots TradingView » que la soumission
veut éviter. Le seul usage légitime de Pine chez nous reste celui déjà acté : prototypage
visuel + contre-validation barre-par-barre via MCP.

Sur vectorbt/Freqtrade : même verdict nuancé. Notre harnais custom suffit ; la SEULE place
où un moteur vectorisé paierait est le batch ORB (rang 5 : ~100+ combos × actifs × WF) —
à décider quand on y arrive, pas avant.

## Verdicts (10 candidats)

| # | Candidat | Type | Source | 5Q / valeur vs labo | Verdict |
|---|---|---|---|---|---|
| F1 | AlgoBuilder [Trend-Following] — Fractalyst | « moteur » TF | **Fermé, invite-only payant** (funnel fractalyst.net) | Inauditable : repaint invérifiable, logique inextractible, importable nulle part. Même s'il était bon, on ne pourrait rien en faire | **POUBELLE** (opacité) |
| F2 | Backtesting System — HALDRO | connecteur backtest | Open source, 1.6k likes, v6 | Bien fait (ext source ±1, ~15 types de SL/TP, filtres). Mais c'est un sous-ensemble de notre labo, dans un environnement aux données plus pauvres. Utilité résiduelle : harnais visuel MCP pour tester vite un indicateur communautaire sans le porter | **OUTIL d'appoint** (proto visuel), jamais moteur de vérité |
| F3 | Ultimate Strategy Template — Daveatt | connecteur backtest | Open source | Même concept que F2 (c'est l'original, plus ancien). L'Advanced Edition ajoute la plomberie d'alertes vers bots (Autoview/Pineconnector/webhooks) — sans objet : 0rum exécute via son propre worker | **DOUBLON** de F2 |
| F4 | Strategy Comparator — CryptoStatistical | comparateur 18 stratégies | Open source | Machine à comparaisons multiples : classe 18 configs × N actifs par métrique in-sample et « fait remonter la meilleure » = sélection biaisée industrialisée (l'anti-leçon n°16). Utilisé comme prévu, sa valeur est NÉGATIVE | **POUBELLE méthodologique** |
| F5 | Deeptest — Fractalyst | librairie métriques | Open source (funnel du F1 payant — leçon n°14) | 100+ métriques, Monte Carlo (reshuffle des trades), « WFA » (découpage 12 fenêtres IS/OOS sans ré-optim). Les métriques, on les a ou on peut les ajouter. **Le nugget : le MC-reshuffle appliqué à NOS listes de trades** (voir extraction E1) | **IDÉE** (concept à porter, pas la lib) |
| S1 | NQ HMA Midday — QuantByBoji | stratégie NQ intraday | Open source, 643 likes, auteur honnête (Python+WF à côté, warnings explicites) | HMA/EMA/ROC momentum, entrées 10:30–13:00 ET, 1 trade/jour, PF 1.53 2025-26. Auteur admet la ré-optimisation périodique = stationnarité faible. NQ intraday = notre trou de données. **Le nugget : la fenêtre MIDDAY** — toutes nos ancres session sont des opens ; lui évite l'open délibérément | **IDÉE** (extraction E2), la meilleure des 5 stratégies |
| S2 | Precision Confluence [JOAT] | stratégie confluence | Open source | CPR+HMA+WaveTrend+divergence+ADX+volume+FVG+OB+sweeps+MTF = empilement de 9 facteurs dont 4-5 de la même famille momentum (recomptage). Notre portfolio_sim a validé la confluence à 3 facteurs ORTHOGONAUX validés un par un — l'inverse de ceci. Chaque brique est déjà au catalogue | **DOUBLON** (famille n°11) |
| S3 | Bastion Execution Protocol [JOAT] | stratégie confluence | Open source, Pine v6 | Même template confluence, mais une brique absente de tout le catalogue : **CVD (cumulative volume delta)** en confirmation order-flow. Sur Binance on a les aggTrades → CVD RÉEL calculable (TV l'approxime). Voir extraction E3 | **IDÉE mineure** (CVD), reste DOUBLON |
| S4 | SuperATR 7-Step — presentTrading | SuperTrend adaptatif | Open source (auteur = usine à contenu, ~1 « stratégie »/semaine) | Contredit nos données deux fois : (a) TP en 7 paliers = plafonne la queue droite, or notre edge validé EST la queue droite (Donchian avg win +3.39R, W/L 4.1×) ; (b) sorties partielles fréquentes = broyeur de frais. Supertrend simple déjà benchmarké et battu par Donchian | **POUBELLE** (anti-profil) |
| S5 | Undertow Backtest — grieto | WaveTrend+RSI MR | Open source, warnings anti-curve-fitting sincères | L'honnêteté méthodologique ne change pas la famille : contre-tendance oscillateur = D chez Oxfordstrat, et notre screen 4 ans a déjà enterré rsi_revert/bb_revert (PF 0.52-0.59, 0 fold/8). Pas de slippage modélisé, avoué | **POUBELLE polie** (hypothèse déjà testée chez nous, réponse : non) |

## Extractions nettes (le butin réel du batch : 3 items)

### E1 — Monte Carlo reshuffle → calibrer les kill-criteria (origine : concept Deeptest)
Le meilleur du batch, et ce n'est pas une stratégie. Nos kill-criteria actuels (série > 9,
DD > 68% full-notional, trade < −2.7R ; kill global portefeuille DD > 60%) sont calibrés
sur UNE trajectoire historique. Le reshuffle des trades (ordre aléatoire, retours conservés)
donne la DISTRIBUTION des drawdowns et des séries de pertes à espérance égale :
- Port : ~30 lignes Python sur les listes de trades de portfolio_sim v2 (elles existent déjà).
- Livrable : percentiles 95/99 de maxDD et pire série par moteur et pour le panier →
  kill-criteria posés sur la distribution, plus sur l'accident d'un seul tirage.
- Bonus : répond proprement à la réserve documentée « pire année Kelly » (la sim v2 montre
  −38.2% sur l'historique ; le MC dira si c'est un percentile 50 ou 95).
- Limite à documenter : le reshuffle casse l'autocorrélation des trades (clustering de
  régime) → il sous-estime les DD si les pertes clustered. Version 2 : block-bootstrap.
- Coût : ~0.5j. **À faire avant d'augmenter quoi que ce soit sur le paper live.**

### E2 — Hypothèse « fenêtre midday » (origine : QuantByBoji)
Toutes les ancres de session du catalogue (n°7/8/12/13/16) sont des OPENS. QuantByBoji
prend le contre-pied : entrées 10:30–13:00 ET seulement, après que la poussière de l'open
est retombée. Hypothèse falsifiable : le momentum post-open (10:30–13:00 ET) a-t-il une
espérance différente de l'open (9:30–10:30) sur NOS actifs ?
- S'intègre tel quel dans le rang 3 (saisonnalité intraday BTC/or par heure UTC) — ajouter
  le découpage en fenêtres de session US, pas seulement par heure.
- Si le rang 3 montre un signal midday sur BTC → seulement alors tester HMA/ROC momentum
  dans la fenêtre (coût marginal, harnais existant).
- NB : ne PAS acheter ses paramètres (il dit lui-même qu'ils périment) ; acheter la fenêtre.

### E3 — CVD comme axe volume n°2 (origine : Bastion [JOAT])
Notre usage du volume : filtre vol>SMA9 invalidé ; volume-par-prix (POC) au rang 8. Le CVD
(delta acheteur/vendeur agresseur) est un 3e axe, jamais évalué au catalogue. On a les
aggTrades Binance → CVD exact (pas l'approximation TV).
- Test minimal : sur les entrées Donchian daily BTC et AK 4h, l'alignement du CVD (20/50
  barres) au moment de l'entrée sépare-t-il les trades gagnants des perdants ? (analyse
  ex-post sur trades existants, ~0.5j, AVANT d'en faire un gate).
- A priori modéré : en daily/4h le CVD est très corrélé au momentum prix (recomptage
  possible). Le test ex-post le dira sans risque.

## Ce que ce batch confirme (méta)

1. **La ferme de funnels continue** : Fractalyst publie la lib gratuite (Deeptest) qui
   crédibilise le produit fermé payant (AlgoBuilder). Même mécanique que nolan.vader
   (leçon n°14). Le badge « open source » d'un satellite ne valide pas le noyau vendu.
2. **Le comparateur de stratégies est un piège de sélection** : classer N configs par
   métrique de backtest et prendre la meilleure = ce que notre règle walk-forward interdit.
   Qu'il soit bien codé n'y change rien.
3. Les deux seuls auteurs méthodologiquement honnêtes du batch (QuantByBoji, grieto)
   sont aussi les seuls à publier des warnings qui DIMINUENT l'attractivité de leur script.
   Corrélation déjà observée au batch 1 — bon heuristique de tri, pas une preuve.

## Décision recommandée

- Ne rien adopter des 5 frameworks. F2 (HALDRO) en marque-page comme harnais visuel MCP
  d'appoint, sans priorité.
- Injecter E1 (MC kill-criteria, ~0.5j) en tête de « restent à faire » — c'est de
  l'infrastructure de survie du paper live, pas de la recherche de signal.
- E2 → absorbé par le rang 3 existant (saisonnalité). E3 → étude ex-post 0.5j, opportuniste.
- Le plan PLAN_BACKTESTS.md reste inchangé sur le fond : aucune des 10 soumissions ne
  déplace les rangs 5 (batch ORB) et 8 (batch niveaux).

## Sources
- https://www.tradingview.com/script/J1pJD2m0-AlgoBuilder-Trend-Following-Fractalyst/ (fermé)
- https://www.tradingview.com/script/GRTkk75x-Backtesting-System/
- https://www.tradingview.com/script/2lGyzYkC-Ultimate-Strategy-Template/ et XIJrNXQc (Advanced)
- https://www.tradingview.com/script/4Dn1ujK3-Strategy-Comparator/
- https://www.tradingview.com/script/fQ7ig92H-Deeptest/
- https://www.tradingview.com/script/pjZmjlZB-NQ-HMA-Midday-Strategy/
- https://www.tradingview.com/script/Eexf5DPB-Precision-Confluence-Trading-Strategy-JOAT/
- https://www.tradingview.com/script/qrCrfG0m-Bastion-Execution-Protocol-JOAT/
- https://www.tradingview.com/script/FDYGrZVD-SuperATR-7-Step-Profit-Strategy-presentTrading/
- https://www.tradingview.com/script/0rGWkv97-Undertow-Backtest/
