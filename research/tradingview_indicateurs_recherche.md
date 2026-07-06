# Indicateurs TradingView — recherche approfondie (preuves vs popularité)

_Date : 2026-07-03. Complément de `oxfordstrat_synthese.md`. Contexte : bot AK MACD 15m BTC._

## 1. Le paysage

TradingView ≈ 150 000+ scripts publiés, >50% open source (Pine v6). Créateurs de référence
("Wizards", 28 au total) : LazyBear (Squeeze Momentum, WaveTrend), LuxAlgo (SMC, Nadaraya-
Watson), jdehorty (ML Lorentzian), KioseffTrading (volume profile), ChrisMoody (RSI MTF),
RicardoSantos (ZigZag). Popularité ≠ preuve : le n°1 (SMC LuxAlgo) n'a aucune étude
indépendante quantifiée.

## 2. Le piège n°1 : le repainting

Les indicateurs à noyau/kernel (Nadaraya-Watson, certains ML, ZigZag, tout ce qui utilise
des pivots non confirmés) **recalculent le passé** : le chart historique montre des signaux
qui n'existaient pas en temps réel. Les backtests visuels sur TV sont donc souvent des
mensonges involontaires. Versions "non-repainting" existent (Julien_Eche, LuxAlgo option) —
toujours vérifier. Règle : n'évaluer un indicateur TV que sur (a) version non-repainting,
(b) backtest barre-par-barre hors TV (notre pipeline Python).

## 3. Ce qui a des preuves quantifiées (BTC, frais inclus)

Backtests Boring Edge, BTC/USDT daily Binance 2017–2026, frais 0.1% RT :

| Stratégie | CAGR | MaxDD | Trades | Notes |
|---|---|---|---|---|
| Buy & Hold | 37.6% | −83.2% | 1 | référence |
| **RSI trend-following** | **53.2%** | ? | 182 | meilleur brut |
| **Donchian breakout (Turtle)** | **48.2%** | ? | ? | meilleur ajusté risque |
| RSI range-momentum | 39.5% | ? | peu | |
| Supertrend (10, 3.0) | 33.0% | −61.5% | 38 | win 42%, W/L 4.1x, 49.5% du temps en position |
| 200 SMA | 26.1% | ? | ? | |

Lecture : sur BTC daily, le **trend-following simple bat ou approche le B&H avec 20+ points
de drawdown en moins**. Supertrend perd sur les entrées tardives et les whipsaws 2021, mais
son profil (peu de trades, gros W/L) est exactement celui qui survit aux frais.

Autres données :
- **Squeeze Momentum (LazyBear)** : PF 1.2–2.3 rapportés sur XBTUSD/ETHUSD 1h–4h, DD ~12% ;
  un filtre MA(50) améliore nettement (sources moins rigoureuses que Boring Edge — à
  confirmer nous-mêmes). 76k likes, basé sur le TTM Squeeze de John Carter.
- **RSI** : RSI-14 horaire sur stocks DJIA : 53% win, 1283% sur 26 ans (vs 881% B&H).
  RSI-2 seuils 15/85 : 91% win mais gains minuscules — cohérent avec Oxfordstrat
  (win rate élevé ≠ profit, et la contre-tendance meurt aux frais en intraday).
- **ML Lorentzian Classification (jdehorty)** : "Most Valuable" Pine 2023. Résultats
  mitigés (TradeSearcher, 96 backtests) : mieux sur daily et actions que sur crypto
  intraday. kNN sur features (RSI/WT/CCI/ADX) en distance de Lorentz — idée intéressante,
  preuve d'edge non établie. Attention aux modes avec lookahead dans l'entraînement.

## 4. Populaires mais SANS preuve indépendante

- **SMC/ICT (LuxAlgo & clones)** : order blocks, fair value gaps, BOS/CHoCH, liquidity.
  Recherche explicite : **zéro étude académique ou backtest indépendant** trouvé — que du
  contenu éducatif/marketing. Le concept FVG ("le prix revient combler l'imbalance")
  contredit frontalement la donnée Volatility Clustering d'Oxfordstrat (continuation >
  reversal après mouvement large). Certains sous-composants sont testables (ex. displacement
  = wide-range bar → continuation, ça on a des données pour).
- **Market Cipher** (WaveTrend + RSI + MFI rebrandé, payant) : pas de backtest indépendant.
- WaveTrend/QQE : cités partout, données quantifiées éparses et faibles.

## 5. Grille d'évaluation (pour toute soumission d'indicateur TV)

1. **Repaint ?** (kernel, pivots non confirmés, security() sans lookahead off)
2. **Backtest indépendant hors TV ?** (pas le strategy tester visuel seul)
3. **Survit aux frais à notre fréquence ?** (petits gains fréquents = mort sur 15m)
4. **Info nouvelle vs nos signaux ?** (un 2e oscillateur momentum ≈ MACD redondant)
5. **Complexité justifiée ?** (Supertrend simple bat la plupart des usines à gaz)

## 6. Convergence Oxfordstrat × TradingView

Les deux corpus pointent le même profil gagnant sur BTC :
**structure de prix + normalisation ATR, peu de trades, winners longs, stops larges** —
Donchian/Turtle, Supertrend, Livermore pivots = la même famille. Et les deux corpus
condamnent la même chose : contre-tendance fréquente, filtres cosmétiques, défauts d'usine.

Candidats concrets pour le bot (tous portables en Python en <100 lignes, testables dans
`backtest_ak_macd.py`) :
- **Supertrend** comme filtre directionnel HTF (remplace/complète l'EMA30 baseline) ou
  comme trailing exit (remplace le TP 1.5R — double usage aligné leçon Kaufman).
- **Donchian 20-55** : benchmark obligatoire — si AK MACD ne bat pas Donchian sur nos
  données, le cerveau actuel ne paie pas sa complexité.
- **Squeeze** (BB dans Keltner) : détecteur de régime compression→expansion, à tester en
  veto (préférer trader la sortie de squeeze, éviter le chop du squeeze).
- **Lorentzian** : plus tard, éventuellement en confirmation — pas en fondation.

## 7. Atout maison : le MCP TradingView

On a un pont direct vers TV Desktop (78 outils) : `pine_set_source`/`pine_smart_compile`
pour injecter et compiler du Pine, `data_get_study_values`/`data_get_pine_*` pour lire les
valeurs de n'importe quel indicateur visible, `replay_*` pour du bar-by-bar. On peut donc :
(a) valider visuellement un indicateur communautaire, (b) extraire ses valeurs et les
comparer barre par barre à notre implémentation Python, (c) rejouer les trades passés du
bot avec l'indicateur superposé. Prototypage TV → validation Python → production bot.

## Sources principales

- https://boringedge.com/bitcoin-supertrend-strategy-backtest/
- https://www.quantifiedstrategies.com/supertrend-indicator/
- https://www.quantifiedstrategies.com/trading-indicators/
- https://tradesearcher.ai/strategies/2019-lorentzian-classification-strategy
- https://www.tradingview.com/script/WhBzgfDu-Machine-Learning-Lorentzian-Classification/
- https://www.tradingview.com/scripts/editors-picks/
- https://www.tradingview.com/wizards/
- https://blog.pickmytrade.trade/squeeze-momentum-strategy/
- https://medium.com/@yashaswa/backtesting-the-viral-nadaraya-watson-envelop-trading-indicator-in-python-b800a70e8167
- https://www.luxalgo.com/library/indicator/smart-money-concepts-smc/
- https://www.writofinance.com/fair-value-gap-in-trading/
