# Session Handoff — AK MACD bot (port Pine → Python)

_Date: 2026-06-17. Reprends ici après relance de Claude._

## Ce qui a été fait
Porté le cerveau de la stratégie **AK MACD 15m** de Pine vers Python. Le bot
analyse désormais les bougies lui-même et décide BUY/SELL ; TradingView n'est
plus que référence visuelle. Tout le downstream (parse → dedup → validate →
bracket 1.5R → PaperExecutor) est réutilisé inchangé.

Projet réel : `.sandbox/hermes-one-shot-home/hermes-trading/`

**Fichiers créés / modifiés :**
- `hermes_trading/external/ak_macd.py` — le cerveau (EMA30 baseline, bande ATR
  couleur, MACD slope flip, volume>SMA9, séquence STRICTE trend→pullback→flip,
  recent_low/high). Émet le payload contract identique au Pine.
- `hermes_trading/external/ak_macd_producer.py` — boucle producteur : fetch 15m
  Binance, drop bougie en cours, route vers ExternalOrchestrator. Mode shadow/live.
- `hermes_trading/external/signal.py` — ajout `ExternalSignalSource.LOCAL = "local"`.
- `scripts/run_local.sh` — lance le producteur (au lieu du vieux bridge TV).
  Mode shadow par défaut ; `AK_MACD_LIVE=1 ./scripts/run_local.sh` pour le live.
- `state/strategy.yaml` — `position_size_r: 2.0` (2% par trade, fidèle à la vidéo).
- `state/goal.yaml` — `allowed_external_sources: [tradingview, local]`,
  `risk_per_trade_max: 0.02`.
- `tests/test_ak_macd.py` — 8 tests. **304 tests passent.**
- `scripts/replay_ak_macd.py` / `scripts/backtest_ak_macd.py` — replays historiques.

## État runtime (au moment du handoff)
- Le bot tourne **EN LIVE (paper)** via `run_local.sh` (superviseur auto-restart).
- 4 process : worker (`run`, superviseur de risque) + hermes_watch + dashboard
  (http://127.0.0.1:8787) + `ak_macd_producer --live --interval 30`.
- Restart propre : `pkill -f "scripts/run_local.sh"; pkill -9 -f hermes_trading`
  puis `AK_MACD_LIVE=1 nohup ./scripts/run_local.sh > state/run_local.out 2>&1 &`
  (macOS n'a pas `setsid`, utiliser `nohup`).

## Premier trade live (preuve que ça marche)
- BUY BTC @ 66 036,87 (bar 1781721000000), risque 200 USD = 2% ✓.
- Fermé via **stop_loss** @ 64 666,67 → **−230 USD (−2,3%)**. Cycle complet OK.
- `received: 1` + `duplicate: 29` : le dedup marche (1 signal = 1 trade max/bougie).
  Le spam "dropped duplicate" est NORMAL (poll 30s sur une bougie 15m).

## Points ouverts (à traiter à la reprise)

### 1. Fill du stop dépasse le niveau (décision: NE PAS toucher pour l'instant)
Le worker (`loop.py close_position_if_needed`, mode bracket) ferme au **close
courant**, pas au niveau du stop figé → sur un mouvement rapide la perte dépasse
les 2% (ici −2,3% au lieu de −2,0%). `bracket.py` promet pourtant perte=risk_usd.
Fix proposé : remplir au `stop_loss_price`/`take_profit_price`, idéalement
déclencher sur touch low/high 1m. ⚠️ Code cœur (natif + externe) → impact
analysis d'abord (règle GitNexus du repo). **Reporté par cube.**

### 2. "Il aurait dû shorter" — faiblesse de l'entrée (à creuser)
Sur le bar du signal (1781721000000), le LONG a fait un **faux rebond** :

| idx | close | baseline | couleur | macd | flipU | flipD |
|-----|-------|----------|---------|------|-------|-------|
| 990 | 65864 | 65425 | BLUE | 214.8 | F | F |
| 993 | 66104 | 65526 | BLUE | 232.4 | T | F |
| 994 | 65301 | 65512 | RED  | 176.2 | F | T |
| 995 | 65404 | 65505 | RED  | 138.3 | F | F |
| **996** | **66037** | **65539** | **BLUE** | **157.6** | **T** | F | ← SIGNAL (BUY) |
| 997 | 65516 | 65538 | gray | 129.3 | F | T |
| 998 | 65366 | 65527 | RED  | 93.7 | F | F |
| 999 | 64793 | 65479 | RED  | 19.1 | F | F |

- LONG validé : flip_up=T, macd>0 (mais **+157 en train de RETOMBER**, lag),
  close>baseline, vol_ok, sequenced_long=T.
- SHORT **pas encore validé** au bar 996 : flip_down=F, macd<0=F, close<baseline=F
  (seul sequenced_short=T). Le short ne se confirme qu'aux bars 997-999 (trop tard).
- Après : −0,79%, −1,02%, −1,88% → stop.

**Diagnostic** : en marché qui retourne (topping), l'entrée long fire sur le
rebond car le MACD reste positif par lag, alors que le prix roule déjà. Pistes :
exiger un MACD qui monte plus franchement, filtre tendance higher-timeframe, ou
ne pas trader long quand le macd décélère. **C'est le vrai sujet stratégie à
travailler.**

## Décisions de cube
- Garder 2% par trade (pas 0.5%).
- Laisser tourner en live (paper), juger sur les trades accumulés — PAS sur le
  backtest 31j (−11%, jugé non représentatif / un seul régime baissier).
- Stop-fill : ne pas corriger maintenant.
