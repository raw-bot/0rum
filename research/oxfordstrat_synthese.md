# Synthèse Oxfordstrat — leçons pour 0rum / AK MACD

_Date : 2026-07-03, complétée 2026-07-04 (2e passe ciblée). Source :
https://oxfordstrat.com/resources/ — 36 études lues sur ~90 (le reste = variantes Part 2/3
des mêmes familles, jugées à rendement marginal nul)._
_Contexte : bot AK MACD 15m BTC (paper live), faiblesse diagnostiquée = long sur faux rebond
au topping (lag MACD), short confirmé trop tard. Voir SESSION_HANDOFF.md._

## Nature de la source

Oxford Capital stress-teste des stratégies classiques du domaine public sur **42 futures US,
daily, 1980–2015+**, avec/sans coûts ($50–100 round-turn), note A–D. Quasiment tout finit C/D.
La valeur n'est pas "quoi copier" mais **quelles croyances sont réfutées par les données**.

⚠️ Caveat permanent : daily futures ≠ 15m BTC. Les leçons retenues ici sont structurelles
(coûts, asymétries, horizons) — à re-vérifier sur nos données Binance via
`scripts/backtest_ak_macd.py` / `replay_ak_macd.py` avant toute décision.

## Partie 1 — Leçons directement applicables au bot actuel

### 1. Les profit targets fixes dégradent le trend following
Étude : Profit-Taking Perry Kaufman (1980–2011). "Simple profit targets reduce absolute
returns and risk-adjusted returns" — sur toute la grille de paramètres.
→ **Notre bracket TP 1.5R coupe les winners.** Levier n°1 : tester trailing exit
(canal, flip couleur ATR déjà calculé, MA cross) à la place du TP fixe.

### 2. Les gros mouvements continuent, ils ne reversent pas
Étude : Volatility Clustering (1980–2019). Après un wide-range bar :
continuation PF 1.15 / DD −15% vs reversal PF 0.86 / DD −271%.
→ **C'est notre trade perdant** (bar 994 = gros rouge, bot a acheté le rebond 996).
Idée : veto "pas de long dans les N bougies après un wide-range bar baissier" (et symétrique).
Version active : le WR bar comme détecteur de régime → mode continuation-only pendant N bougies.

### 3. MACD 12/26 = à éviter ; horizons longs >> courts
MACD Part 1 : 12/26 → CAGR 5.1%, DD −84% ; 80/160 → CAGR 17.1%, DD −49%.
MACD Part 2 (croisement signal-line) : noté **D**.
→ Vérifier les périodes de `ak_macd.py` ; si 12/26, tester élargissement ou analyse sur
timeframe supérieur avec exécution 15m.

### 4. Les filtres populaires n'ajoutent rien — volume inclus
- ADX : **D**, "does not add value to the base case trend-following model".
- Volatility squeeze : aucune amélioration (C).
- **Volume filters : SANS filtre CAGR 17.8%, AVEC 12.7%** — le filtre volume élimine
  surtout des bons trades.
→ Notre condition `volume > SMA9` vient de la vidéo, pas des données. **A/B test prioritaire.**
(Le volume crypto spot est peut-être plus informatif que le volume futures daily — tester.)

### 5. Dual momentum (2 horizons) > simple momentum
Étude : Dual Momentum & Vortex (1980–2020) : CAGR 16.7%. Horizon lent = filtre directionnel,
horizon rapide = signal. Holding >50 bars préféré.
→ **Valide le filtre HTF du handoff** : EMA 1h/4h comme condition directionnelle du signal 15m.

### 6. La contre-tendance meurt sous les coûts
Turtle Soup : viable sans frais, **D avec frais**. Bollinger %b reversal : **D**.
RSI-2 Connors : 70% win rate mais CAGR 0.34%. Friday Momentum : **D**, "stopped working
once realistic cost of trading is applied".
→ Sur 15m avec frais, éviter tout modèle à petits gains fréquents. Notre profil
(peu de trades, gros mouvements) est structurellement le bon.

### 7. Meilleur système du lot : Livermore (pivots swing + bruit ATR)
CAGR 17.2%, DD −44%, win 41%, **coûts inclus** (1980–2020). Pivots de swing en multiples
d'ATR, entrée sur pénétration de 2 pivots + marge de bruit, sortie sur pivot opposé.
Zéro indicateur laggé — pure structure de prix normalisée volatilité.
→ Candidat pour un **2e cerveau** branché en shadow mode via ExternalOrchestrator,
comparé à AK MACD sur les mêmes bougies live.

## Partie 2 — Leçons qui remettent en cause la structure même du bot

### 8. La diversification est le moteur caché de tous les bons résultats
Tous les CAGR ~17% du site viennent d'un **portefeuille de 42 marchés décorrélés** avec
sizing 1% fixed-fractional. Aucune stratégie mono-marché ne tient ces chiffres.
→ Le bot est mono-asset (BTC). Piste structurelle majeure : même cerveau sur un panier
(ETH, SOL, …), risque par trade réduit en proportion. Le producteur Binance est
généralisable ; c'est une extension d'infra, pas une réécriture.

### 9. Le timeframe est un choix de coût, pas de style
Tout ce qui marche sur le site est daily. Le 15m trade ~96x plus souvent → le drag de
frais est multiplié d'autant. Les études courtes-durées (ORB **D**, One Night Stand **D**,
Long Equity **C** malgré 80% win) meurent toutes aux coûts.
→ Question ouverte : le même cerveau AK MACD sur bougies 1h/4h (exécution inchangée)
réduirait mécaniquement le nombre de trades et le poids des frais. À backtester.

### 10. L'intermarché a existé puis est mort — les edges décèdent
Pathfinder (devises filtrées par T-bonds) : PF 1.81 en 1980–1995, PF 1.03 en 1996–2015.
→ Deux lectures : (a) transposition crypto possible — BTC dominance, ETH/BTC, funding,
DXY comme filtre directionnel exogène que le bot n'utilise pas du tout aujourd'hui ;
(b) tout edge déployé doit être surveillé en walk-forward, il peut mourir.

### 11. Asymétrie long/short
Le Long Equity System est long-only par construction (drift actions). BTC a un drift
historique similaire. Notre bot est symétrique long/short avec les mêmes règles.
→ Piste : règles/filtres différenciés par direction (ex. shorts uniquement en mode
continuation post-WR bar, longs avec filtre HTF).

## Plan d'action proposé (du moins cher au plus ambitieux)

1. **A/B replays sur l'existant** : (a) sans filtre volume, (b) MACD élargi,
   (c) TP 1.5R → trailing, (d) veto WR-bar contraire. Zéro code cœur nouveau.
2. **Filtre HTF** (EMA 1h/4h) — aligné diagnostic handoff + données.
3. **Cerveau Livermore crypto** en shadow mode à côté d'AK MACD.
4. **Multi-asset** : même cerveau sur panier crypto, sizing réduit par jambe.
5. **Régime WR-bar** : mode continuation-only après wide-range bar.
6. Exploratoire : filtre directionnel exogène (dominance, funding, DXY).

## Index des études lues (26)

| Étude | Note | Verdict utile |
|---|---|---|
| Adaptive MA (KAMA) | C | Lag réduit ≠ meilleur |
| Zero-Lag MA | C | CAGR 17.2% mais pas mieux que Hull |
| Multiple Time Frames (Babcock) | C | MTF simple, résultats moyens |
| ADX Filter | D | N'ajoute rien |
| Linear Regression Slope normalisée | C | Pente/ATR = mesure propre de "MACD décélère" |
| MACD Part 1 | C | 12/26 à éviter ; 80/160 >> |
| MACD Part 2 (signal line) | D | Croisement signal = pire |
| Keltner 3-phase | C | Structure pullback correcte |
| Profit-Taking Kaufman | — | TP fixes dégradent tout |
| Volatility Squeeze | C | Filtre inutile |
| 3-Bar Momentum | C | Filtre tendance aide ; holds longs > courts ; decay post-2007 |
| Turtle Soup | D | Contre-tendance tuée par coûts |
| False Breakout | C | Marginal |
| Wyckoff Mean Reversion | C | Marginal |
| Bollinger %b reversal | D | Tué par coûts |
| Donchian combiné | C | Benchmark honnête |
| Livermore Part 1 | C | **Meilleur profil du site coûts inclus** |
| Volatility Clustering 1 | — | **Continuation >> reversal après WR bar** |
| Volume Filters 1 | C | **Filtre volume détruit de la perf** |
| RSI-2 | C | Win rate élevé, gains nuls |
| Dual Momentum Vortex | C | 2 horizons > 1 |
| Pathfinder intermarché | C | Marchait, puis mort (1996+) |
| Friday Momentum / One Night Stand | D | Calendaire tué par coûts |
| Opening Range Breakout | D | Session-based, faible |
| Long Equity System | C | 80% win, perf décevante ; long-only par drift |
| Dow Theory MTF | C | Pivots multi-échelles, moyen |
| **2e passe (2026-07-04)** | | |
| Global Market Correlations 1995-2014 | — | Corrélations DYNAMIQUES entre 8 secteurs/56 instruments ; méthode réutilisable pour notre panier : corr sur fenêtre 256 barres de log-returns 10 barres |
| Volatility Clustering 2 | — | Continuation confirmée en force : PF 1.13-1.17 / DD 7-14% vs reversal PF 0.85-0.88 / DD 137-244%. Les filtres σ n'améliorent PAS le modèle de base |
| Volatility Clustering 3 | — | L'ORB n'améliore PAS le modèle continuation ; horizons 2-4j >> 1j (Sharpe -0.09 → 0.51) |
| Bullish Engulfing (exits) | **D** | L'engulfing seul n'a AUCUNE valeur prédictive → axe d'ablation pour la First Candle Rule (test avec/sans confirmation engulfing) |
| NR7 (Crabel) | C | "Not tradeable once costs applied" — compression seule insuffisante (cohérent squeeze) |
| Wilder Volatility Breakout (SIC+ARC) | C | Ancêtre ATR-bands/Supertrend, reversal permanent, moyen |
| TD Sequential | — | Coûts : CAGR 8-12% → 2-6%, PF → 1.1-1.4. Séduisant sans frais, faible avec |
| Greatest Swing Value (Williams) | **D** | Benchmark perdant vs ORB |
| Hikkake | C | Holds longs préférés ; filtre tendance REDONDANT ; brique multi-pattern possible |
| Donchian Channel 1 (target exits) | C | Version à cibles % — cohérent : les targets dégradent (Kaufman) |
