# 🔍 Audit Structurel Complet — 0rum Trading Bot

> **Date** : 2026-05-23  
> **Scope** : Tout le code source `src/`, `.env`, configuration  
> **Méthode** : Revue statique ligne par ligne de chaque module

---

## Table des matières

1. [Résumé exécutif](#1-résumé-exécutif)
2. [CRITIQUE — Notionnel hardcodé × 100 (levier implicite)](#2-critique--notionnel-hardcodé--100-levier-implicite)
3. [CRITIQUE — Calcul P&L incohérent et structurellement faux](#3-critique--calcul-pl-incohérent-et-structurellement-faux)
4. [CRITIQUE — Equity statique (jamais mise à jour)](#4-critique--equity-statique-jamais-mise-à-jour)
5. [CRITIQUE — Trades orphelins jamais fermés](#5-critique--trades-orphelins-jamais-fermés)
6. [MAJEUR — Backtesting : biais et incohérences](#6-majeur--backtesting--biais-et-incohérences)
7. [MAJEUR — Risk Management incomplet](#7-majeur--risk-management-incomplet)
8. [MAJEUR — Data Pipeline (proxy PAXG ≠ XAUUSD)](#8-majeur--data-pipeline-proxy-paxg--xauusd)
9. [MINEUR — Sécurité et opérations](#9-mineur--sécurité-et-opérations)
10. [MINEUR — Problèmes de concurrence](#10-mineur--problèmes-de-concurrence)
11. [MINEUR — Qualité de code et maintenabilité](#11-mineur--qualité-de-code-et-maintenabilité)
12. [Tableau récapitulatif](#12-tableau-récapitulatif)
13. [Recommandations prioritaires](#13-recommandations-prioritaires)

---

## 1. Résumé exécutif

L'architecture de 0rum est **globalement bien structurée** : séparation en 7 couches, données Pydantic vs ORM, circuit breaker Redis, walk-forward optimizer avec Monte Carlo. Le code est bien documenté et suit les conventions du projet.

**Cependant, l'audit révèle 28 findings dont 8 critiques** qui, pris ensemble, faussent fondamentalement la simulation de trading :

| Sévérité | Nombre | Impact |
|---|---|---|
| 🔴 CRITIQUE | 8 | Résultats de backtesting / sizing / P&L structurellement faux |
| 🟠 MAJEUR | 9 | Risques de pertes non détectées, biais significatifs |
| 🟡 MINEUR | 11 | Maintenance, sécurité périphérique, robustesse |

> [!CAUTION]
> **Le problème central** : le bot opère avec un multiplicateur **× 100** hardcodé partout (`1 lot = 100 oz XAUUSD`), ce qui crée un **levier implicite de ~330×** sur un compte de $10 000. Combiné à une equity qui ne se met **jamais** à jour en fonction des P&L réalisés, le système accepte et dimensionne des trades comme si le capital était constant et infini.

---

## 2. CRITIQUE — Notionnel hardcodé × 100 (levier implicite)

### 2.1 Le multiplicateur `× 100` dispersé dans tout le code

Le facteur `Decimal("100")` apparaît **7 fois** dans 3 fichiers distincts, toujours en dur :

| Fichier | Ligne | Usage |
|---|---|---|
| [sizer.py](file:///Users/cube/Documents/00-code/0rum/src/risk/sizer.py#L92) | 92 | `risk_amount / (sl_distance * Decimal("100"))` |
| [account.py](file:///Users/cube/Documents/00-code/0rum/src/execution/paper/account.py#L54) | 54 | `entry_price * size_lots * Decimal("100")` (notionnel) |
| [account.py](file:///Users/cube/Documents/00-code/0rum/src/execution/paper/account.py#L63) | 63 | `|entry - stop| * size_lots * Decimal("100")` (stop risk) |
| [account.py](file:///Users/cube/Documents/00-code/0rum/src/execution/paper/account.py#L83-L84) | 83-84 | P&L réalisé/ouvert après TP1 |
| [account.py](file:///Users/cube/Documents/00-code/0rum/src/execution/paper/account.py#L87) | 87 | P&L non-réalisé position ouverte |
| [jobs.py](file:///Users/cube/Documents/00-code/0rum/src/scheduler/jobs.py#L403) | 403 | `blended_pnl_pct * entry * size_lots * Decimal("100")` |

> [!WARNING]
> ### Problèmes structurels :
> 
> 1. **Levier implicite massif** : avec XAUUSD à ~$3 300, 1 lot = 100 oz → **notionnel = $330 000**. Sur un compte de $10 000, c'est un **levier × 33 par lot**. Avec 5 positions max, le levier agrégé peut atteindre **× 165**.
> 
> 2. **Valeur magique non-configurable** : le `100` n'est défini nulle part comme constante nommée, ni dans `config.py`, ni en `Settings`. Si le bot change de broker ou de convention de lot, il faut chercher dans 3 fichiers.
> 
> 3. **Pas de garde sur le levier** : aucun gate ne vérifie le `exposure_multiple` avant d'ouvrir un trade. L'`exposure_multiple` est calculé dans `account.py` mais **uniquement pour l'affichage dashboard**, jamais utilisé comme gate pré-trade.

### 2.2 Sizing : le calcul de `size_lots` permet des positions disproportionnées

```python
# sizer.py:92 — Le cœur du sizing
size_lots = (risk_amount / (sl_distance * Decimal("100"))).quantize(Decimal("0.01"))
```

**Scénario concret** avec les paramètres par défaut :

```
equity        = $10,000
risk_per_trade = 1% → $100
entry          = $3,300
SL distance    = $5  (typique pour un sl_atr_mult=0.5 × ATR14 M15 ≈ $10)
size_lots      = $100 / ($5 × 100) = 0.20 lots

notionnel = 0.20 × 100 × $3,300 = $66,000  → LEVIER ×6.6 par trade
```

Avec 5 positions max au même sizing → **levier total × 33**. Sans concentration gate (4+ require), c'est permis.

> [!IMPORTANT]
> Le sizing est mathématiquement correct pour l'objectif « 1% de perte au SL ». Mais il **ne valide jamais** que le notionnel résultant est compatible avec le capital ou un ratio de levier raisonnable. C'est une faille classique des position sizers basés sur le risque : ils garantissent la perte max mais ignorent l'exposition.

---

## 3. CRITIQUE — Calcul P&L incohérent et structurellement faux

### 3.1 P&L en pourcentage vs P&L en dollars : deux formules incompatibles

**Dans `_close_trade`** ([jobs.py:388-393](file:///Users/cube/Documents/00-code/0rum/src/scheduler/jobs.py#L388-L393)) :

```python
tp1_pnl = (tp1_price - entry_price) / entry_price * direction_sign
final_pnl = (exit_price - entry_price) / entry_price * direction_sign
# ...
blended_pnl_pct = Decimal("0.5") * tp1_pnl + Decimal("0.5") * final_pnl
```

**Problème** : `pnl_pct` est calculé comme un **rendement sur le prix d'entrée** (ex: `$5 / $3300 = 0.15%`), **pas** comme un rendement sur le capital risqué ni sur l'equity. Ce chiffre est ensuite :

1. **Stocké dans `trade.pnl_pct`** → utilisé dans `evaluate_daily_loss()` pour le gate daily loss
2. **Sommé** dans le daily P&L check → **on additionne des rendements sur le prix de l'actif, pas sur le portefeuille**

**Conséquence** : le daily loss limit de `-3%` compare des pommes et des oranges. Un trade qui perd $5 sur une entrée à $3 300 donne `pnl_pct = -0.0015` (−0.15%), mais la perte réelle sur le portefeuille de $10 000 est bien plus grande (proportionnelle au sizing).

### 3.2 P&L dollars calculé de façon incohérente

```python
# jobs.py:403
trade.pnl = blended_pnl_pct * entry_price * trade.size_lots * Decimal("100")
```

Ce calcul reconvertit le `pnl_pct` (rendement sur le prix) en dollars. Mais `blended_pnl_pct` est déjà normalisé par `entry_price`, donc la formule revient à :

```
pnl_usd = (exit - entry) / entry * entry * size_lots * 100
        = (exit - entry) * size_lots * 100
```

C'est **arithmétiquement correct** pour le P&L en dollars. Mais le `pnl_pct` stocké en base est **inutilisable tel quel** pour le daily loss gate car il n'est pas un rendement sur l'equity.

### 3.3 La gate daily loss est structurellement biaisée

```python
# gates.py:30-37
stmt = select(func.coalesce(func.sum(TradeORM.pnl_pct), 0)).where(...)
daily_pnl_pct = float(result.scalar_one())
passed = daily_pnl_pct > daily_loss_limit  # -0.03
```

On additionne les `pnl_pct` de tous les trades fermés du jour. Mais chaque `pnl_pct` est un rendement sur le prix XAUUSD (ex: -0.15%), pas sur l'equity du compte. Avec un levier implicite × 6.6 par trade, `-0.15%` de rendement prix = **-1.0% de perte equity**. La gate à `-3%` ne se déclencherait qu'après une chute de prix de `-3%/0.15 ≈ 20×` le mouvement attendu.

> [!CAUTION]
> **Impact** : la gate daily loss est essentiellement **inactive**. Un drawdown catastrophique de 10-20% sur l'equity ne déclencherait pas la protection `-3%` parce que les `pnl_pct` sommés restent proches de zéro en termes de rendement sur le prix.

---

## 4. CRITIQUE — Equity statique (jamais mise à jour)

### 4.1 L'equity de sizing est une constante de configuration

```python
# config.py:48
theoretical_equity_usd: Decimal = Decimal("10000")

# runner.py:95 — utilisé pour CHAQUE trade
sizing = calculate_position_size(
    equity=self.settings.theoretical_equity_usd,  # TOUJOURS $10,000
    ...
)
```

L'equity passée au sizer est **toujours** la valeur initiale de `.env`. Même si le paper account accumule des pertes (cash_balance diminue), le sizer continue à risquer 1% de $10 000 = $100 par trade.

### 4.2 Conséquence : augmentation du risque réel après pertes

| Après N pertes | Equity réelle | Risque par trade (fixe) | % réel risqué |
|---|---|---|---|
| 0 | $10,000 | $100 | 1.0% |
| 5 | ~$9,500 | $100 | 1.05% |
| 10 | ~$9,000 | $100 | 1.11% |
| 20 | ~$8,000 | $100 | 1.25% |
| Ruin-path | $5,000 | $100 | **2.0%** |

Le risque réel **augmente** quand le capital diminue — c'est l'inverse d'un money management sain. L'equity devrait être lue depuis le `paper_account_state.equity` (qui **existe** et est calculé correctement dans `account.py`).

> [!IMPORTANT]
> Le paper account dans `account.py` calcule correctement l'equity (starting_balance + realized_pnl + unrealized_pnl), mais cette valeur **n'est jamais injectée** dans le sizer. C'est une déconnexion architecturale critique.

---

## 5. CRITIQUE — Trades orphelins jamais fermés

### 5.1 Le moniteur ne gère pas les trades sans résolution

```python
# walk_forward.py:221
return TradeOutcome.OPEN, 0.0
```

Dans `simulate_signal_mode_trade_outcome`, si aucun niveau (SL, TP1, TP2, trailing) n'est touché pendant les bougies disponibles, le trade reste `OPEN` avec P&L = 0.

**En live** (scheduler `monitor_trades`), un trade qui ne touche ni SL ni TP reste OPEN **indéfiniment**. Il n'y a :

- ❌ **Pas de TTL (time-to-live)** sur les trades ouverts
- ❌ **Pas de close forcée** après N heures/jours
- ❌ **Pas de trailing stop automatique** pour les trades OPEN (seulement après TP1)

### 5.2 Impact sur les gates

Les trades OPEN sont comptés dans `evaluate_max_positions()`. Si des trades restent OPEN indéfiniment, les 5 slots se remplissent et **bloquent tout nouveau signal**. C'est un deadlock de facto.

### 5.3 Pas de close_reason "EXPIRED" ou "MANUAL"

Le schema SQL prévoit `close_reason` avec les valeurs `TP1, TP2, SL, TRAIL, MANUAL, CIRCUIT_BREAKER`, mais aucun code ne ferme un trade avec `MANUAL` ou `CIRCUIT_BREAKER`. Le circuit breaker **empêche de nouveaux trades** mais **ne ferme pas les trades ouverts**.

---

## 6. MAJEUR — Backtesting : biais et incohérences

### 6.1 Simulation vs monitoring live désynchronisés

Le backtesting utilise **deux simulateurs** différents :

| Simulateur | Fichier | Utilisé par |
|---|---|---|
| `simulate_trade_outcome` | [walk_forward.py:172](file:///Users/cube/Documents/00-code/0rum/src/backtesting/walk_forward.py#L172) | **Plus utilisé par l'optimizer** |
| `simulate_signal_mode_trade_outcome` | [walk_forward.py:244](file:///Users/cube/Documents/00-code/0rum/src/backtesting/walk_forward.py#L244) | Optimizer actuel |

`simulate_trade_outcome` est un vestige non supprimé qui simule SL/TP simple sans trailing. L'optimizer utilise maintenant `simulate_signal_mode_trade_outcome`, ce qui est correct, mais le premier reste dans le code comme piège pour les futurs développeurs.

### 6.2 Pas de slippage ni de spread dans la simulation

La simulation compare `candle.high >= tp` et `candle.low <= sl` sans aucun modèle de :
- **Spread** (XAUUSD typique : $0.20-$0.50)
- **Slippage** (exécution au prix marché, pas au SL exact)
- **Gap de prix** (weekend gaps sur or)

Pour XAUUSD avec un ATR H1 de ~$10-15, un spread de $0.40 représente 2-4% du mouvement moyen. Sur des R:R de 1.5:1, ça biaise significativement les résultats.

### 6.3 Biais de survie dans l'optimizer

L'optimizer évalue 100 combos LHS × 3 fenêtres et garde le meilleur WFE. Mais :

- **Pas de correction Bonferroni** pour les comparaisons multiples (100 combos = forte chance de trouver un bon résultat par hasard)
- Le Monte Carlo est censé mitiger ceci, mais il resample les P&L **déjà observés**, pas de nouveaux chemins de prix
- **Pas de test de robustesse au voisinage** : le meilleur combo pourrait être un pic isolé dans l'espace des paramètres

### 6.4 Le backtesting ignore le sizing et le risk management

La simulation de trades dans l'optimizer ne passe **pas** par le RiskGateRunner :

```python
# optimizer.py — les signaux sont simulés directement
_, pnl = simulate_signal_mode_trade_outcome(...)
```

Résultat : les P&L backtestés ne reflètent pas le sizing réel (ATR adjustment, concentration, vol factor). Le backtesting est en mode "1 lot fixe" implicitement.

### 6.5 P&L de backtesting vs P&L live : unités différentes

- **Backtesting** : P&L en "prix units" → `pnl = abs(tp1 - entry)` ou `pnl = -risk`
- **Live** : P&L en pourcentage du prix → `pnl_pct = (exit - entry) / entry`

Le profit factor de l'optimizer et celui affiché dans `strategy_stats` ne sont **pas comparables**.

---

## 7. MAJEUR — Risk Management incomplet

### 7.1 Pas de risk-on-risk : exposition agrégée non contrôlée

Le sizer calcule le risque **par trade**, mais rien n'agrège :
- Le notionnel total ouvert vs l'equity
- Le risque SL total ouvert vs l'equity
- La corrélation directionnelle (5 SELL XAUUSD = concentration massive)

L'`exposure_multiple` est calculé dans `account.py` (affiché au dashboard) mais **jamais vérifié** comme gate pré-trade.

### 7.2 Concentration gate trop permissive

```python
# sizer.py:79
concentration_reduced = same_direction_open_count >= 4
```

La concentration ne **réduit** le risque qu'à partir de 4 positions dans la même direction, et ne **bloque** jamais. Avec un actif unique (XAUUSD), 4 BUY à levier × 6.6 chacun = **levier directionnel × 26** avant réduction.

### 7.3 Pas de max drawdown guard en temps réel

Il n'y a aucun circuit breaker basé sur le drawdown cumulé de l'equity. Le circuit breaker est basé sur le **nombre de stops consécutifs** (8), pas sur la perte en capital. Un scénario de 7 stops consécutifs à 1% chacun = -7% drawdown sans déclenchement.

### 7.4 Pas de kill switch

Aucun mécanisme pour arrêter **immédiatement** tout trading (ni dans l'API, ni dans le dashboard, ni via Redis). Le dashboard est read-only.

---

## 8. MAJEUR — Data Pipeline (proxy PAXG ≠ XAUUSD)

### 8.1 Le proxy PAXG/USDT n'est pas XAUUSD

```python
# market_client.py:22
_INSTRUMENT_MAP = {"XAUUSD": "PAXG/USDT"}
```

PAXG (Paxos Gold) est un token ERC-20 adossé à l'or physique, mais :

- **Spread différent** : PAXG a un spread beaucoup plus large que XAUUSD spot
- **Volume différent** : les volumes PAXG sont une fraction des volumes XAUUSD réels
- **Prix différent** : PAXG trade avec un premium/discount variable par rapport au spot gold
- **Pas les mêmes horaires** : PAXG = crypto 24/7, XAUUSD = forex avec gaps weekend
- **Pas les mêmes microstructures** : les sweeps, breakouts et EMA crossovers sur PAXG ne sont pas ceux de XAUUSD

> [!WARNING]
> Les 4 stratégies sont calibrées (ATR, swings, volumes) sur des données PAXG mais prétendent trader XAUUSD. Les paramètres optimisés par le walk-forward sur données PAXG seront **inapplicables** au XAUUSD réel.

### 8.2 Le volume PAXG est inutilisable pour les stratégies

La stratégie `breakout_expansion` utilise `volume_mult` comme filtre :

```python
# breakout_expansion : seuil volume > volume_mult × SMA_volume(20)
```

Mais le volume PAXG Binance est en tokens PAXG, pas en volume tick XAUUSD. Ce filtre est fondamentalement invalide.

---

## 9. MINEUR — Sécurité et opérations

### 9.1 Credentials dans `.env` non cryptés

```env
# .env
MASSIVE_S3_ACCESS_KEY_ID=16fdb7dd-24e9-438a-b163-edede6f3211a
MASSIVE_S3_SECRET_ACCESS_KEY=z3_bHLoZSG_WvWMXI1710YzQQpDhHf2r
```

Bien que `.env` soit dans `.gitignore`, les clés S3 Massive sont en clair. Si le fichier est copié ou exposé accidentellement, c'est un accès au bucket S3.

### 9.2 Dashboard sans authentification

```python
# dashboard.py:7
# No authentication (D-25 — operator tool, runs locally or behind firewall).
```

C'est un choix documenté (D-25), mais le dashboard expose :
- L'état du compte paper
- Toutes les positions ouvertes et fermées
- Les paramètres de stratégie optimisés
- L'état du circuit breaker

Si le port est accidentellement exposé (Docker port mapping, forwarding), c'est une fuite d'information.

### 9.3 Pas de rate limiting sur l'API

Les endpoints `/api/dashboard` et `/health` n'ont aucun rate limiting. Un scan automatique ou un bot pourrait surcharger le dashboard (et par extension la base de données).

### 9.4 `database_url` visible en log de startup

```python
# main.py:48
database_url=settings.database_url.split("@")[-1],  # hide credentials
```

La partie host/port/dbname est loguée (pas le password), ce qui est acceptable mais pas optimal.

---

## 10. MINEUR — Problèmes de concurrence

### 10.1 Module-level state dans `database.py`

```python
# database.py:8
settings = get_settings()
engine = create_async_engine(settings.database_url, ...)
```

L'engine est créé à l'import du module — avant que `configure_structlog()` ne soit appelé. Si une erreur survient pendant la création de l'engine, le log n'est pas structuré.

### 10.2 `_pipeline_runner` global mutable

```python
# jobs.py:31
_pipeline_runner: "Any | None" = None
```

Ce singleton global est muté par `_set_pipeline_runner()` au startup. C'est thread-safe grâce au GIL mais pas propre en termes de DI.

### 10.3 Sessions DB multiples dans `_close_trade` et `_process_trade`

```python
# jobs.py — _process_trade ouvre sa propre session
async with AsyncSessionLocal() as session:
    async with session.begin():
        trade.status = "TP1_HIT"
        session.add(trade)
```

Le trade object est issu d'une session **différente** (celle de `monitor_trades`), puis `session.add(trade)` le rattache à une nouvelle session. Ceci peut provoquer :
- `DetachedInstanceError` si l'objet a été expiré
- **Dirty reads** entre les deux sessions
- Perte de modifications si les sessions se chevauchent

### 10.4 Race condition entre `monitor_trades` et `run_pipeline`

Les deux jobs tournent toutes les 15 minutes. `run_pipeline` crée de nouveaux trades (status=OPEN). `monitor_trades` lit les trades OPEN et peut les modifier. Si les deux exécutent simultanément, un trade nouvellement créé pourrait être évalué avec une bougie antérieure à son `opened_at` — ce qui est géré par le guard `candle_timestamp <= trade_opened_at` mais de manière fragile.

---

## 11. MINEUR — Qualité de code et maintenabilité

### 11.1 Code mort : `simulate_trade_outcome`

[walk_forward.py:172-221](file:///Users/cube/Documents/00-code/0rum/src/backtesting/walk_forward.py#L172-L221) — L'ancien simulateur simplifié n'est plus appelé par aucun code actif. Il devrait être supprimé pour éviter la confusion.

### 11.2 Duplication de `_calculate_atr`

L'ATR est implémenté dans **3 endroits différents** :

| Implémentation | Fichier | Méthode |
|---|---|---|
| Wilder's EMA smoothing | [base.py:67](file:///Users/cube/Documents/00-code/0rum/src/strategies/base.py#L67) | `AbstractStrategy.calculate_atr()` |
| Simple mean des TRs | [regime_detector.py:81](file:///Users/cube/Documents/00-code/0rum/src/backtesting/regime_detector.py#L81) | `RegimeDetector._calculate_atr()` |
| Simple mean des TRs | [walk_forward.py:224](file:///Users/cube/Documents/00-code/0rum/src/backtesting/walk_forward.py#L224) | `_calculate_atr()` |

Les implémentations diffèrent : la base strategy utilise Wilder's smoothing (EMA), les deux autres utilisent une moyenne simple. Pour le même historique, elles donnent des **valeurs ATR différentes**.

### 11.3 Duplication de `_ema`

L'EMA est implémentée dans `regime_detector.py`, `ema_momentum.py`, et implicitement dans `trend_continuation.py`. Chaque copie est identique mais pas partagée.

### 11.4 Pas de validation des `PARAM_RANGES` à l'exécution

Le StrategyRunner charge les params de l'optimizer sans vérifier qu'ils sont dans les `PARAM_RANGES` déclarées :

```python
# runner.py:96-97
if row is not None:
    return dict(row.params)  # aucune validation !
```

Un optimizer qui écrit des params hors-bornes pourrait créer des signaux aberrants.

### 11.5 `optimizer_result_count` peut être non-défini

```python
# runner.py:87-103
async with AsyncSessionLocal() as session:
    ...
    if row is None:
        optimizer_result_count = int(...)  # indentation: à l'intérieur du `if`

if row is not None:
    return dict(row.params)

if optimizer_result_count > 0:  # ← UnboundLocalError si row is not None
```

Ce bug est masqué car si `row is not None`, on retourne avant d'atteindre `optimizer_result_count`. Mais le code est fragile — un refactoring pourrait l'exposer.

---

## 12. Tableau récapitulatif

| # | Sévérité | Module | Finding | Impact |
|---|---|---|---|---|
| F-01 | 🔴 | sizer / account | Multiplicateur × 100 hardcodé sans guard de levier | Levier implicite × 33 par trade possible |
| F-02 | 🔴 | sizer | Pas de check notionnel/equity avant trade | Exposition incontrôlée |
| F-03 | 🔴 | jobs / gates | pnl_pct = rendement prix, pas rendement equity | Daily loss gate inactive |
| F-04 | 🔴 | runner | Equity statique ($10k) pour le sizing | Risque réel augmente après pertes |
| F-05 | 🔴 | jobs | Trades OPEN sans TTL/expiry | Deadlock potentiel (5 slots bloqués) |
| F-06 | 🔴 | optimizer | Backtesting sans sizing/risk gates | Résultats non-représentatifs |
| F-07 | 🔴 | optimizer | P&L backtesting en "prix units" vs live en "%prix" | Incomparabilité des métriques |
| F-08 | 🔴 | market_client | PAXG/USDT ≠ XAUUSD (prix, volume, microstructure) | Params optimisés invalides pour XAUUSD |
| F-09 | 🟠 | optimizer | Pas de slippage/spread dans la simulation | Biais optimiste 2-4% sur chaque trade |
| F-10 | 🟠 | optimizer | Biais de sélection sur 100 combos sans correction | Overfitting probable |
| F-11 | 🟠 | risk | Pas de max drawdown guard en temps réel | Drawdown illimité entre stops |
| F-12 | 🟠 | risk | Concentration gate trop laxiste (4+) | Levier directionnel × 26 permis |
| F-13 | 🟠 | risk | Pas de kill switch | Impossible d'arrêter le trading en urgence |
| F-14 | 🟠 | risk | exposure_multiple non utilisé en gate | Décoration dashboard seulement |
| F-15 | 🟠 | jobs | pnl_pct stocké = rendement sur prix actif | Toutes les métriques aval sont fausses |
| F-16 | 🟠 | risk | Pas d'equity drawdown circuit breaker | Seuls les stops consécutifs comptent |
| F-17 | 🟠 | risk | Circuit breaker ne ferme pas les positions | Bloque les nouvelles mais pas les ouvertes |
| F-18 | 🟡 | .env | Clés S3 en clair dans .env | Risque si fichier exposé |
| F-19 | 🟡 | dashboard | Pas d'authentification | Fuite info si port exposé |
| F-20 | 🟡 | dashboard | Pas de rate limiting | DoS potentiel |
| F-21 | 🟡 | jobs | Sessions DB croisées dans _process_trade | DetachedInstanceError potentiel |
| F-22 | 🟡 | jobs | Race condition monitor_trades / run_pipeline | Guard fragile |
| F-23 | 🟡 | base/regime | 3 implémentations ATR divergentes | Résultats incohérents |
| F-24 | 🟡 | walk_forward | Code mort (simulate_trade_outcome) | Confusion maintenabilité |
| F-25 | 🟡 | runner | Params non validés contre PARAM_RANGES | Params hors-bornes possibles |
| F-26 | 🟡 | runner | optimizer_result_count potentiellement non défini | Bug latent |
| F-27 | 🟡 | database | Engine créé avant structlog configuré | Logs non-structurés au startup |
| F-28 | 🟡 | ema_momentum/regime | Duplication EMA helper | Maintenance |

---

## 13. Recommandations prioritaires

### 🔴 Priorité 1 — Corriger la fondation (avant tout backtesting)

1. **Centraliser le contract size** : extraire `LOT_SIZE_OZ = 100` dans `config.py` ou même dans un dict `INSTRUMENT_SPECS["XAUUSD"]` avec `contract_size`, `tick_value`, `spread_model`

2. **Ajouter un gate de levier** : dans `RiskGateRunner.evaluate()`, calculer le notionnel post-trade et rejeter si `(notionnel_existant + nouveau_notionnel) / equity > MAX_LEVERAGE` (ex: 10×)

3. **Passer l'equity dynamique au sizer** : au lieu de `self.settings.theoretical_equity_usd`, lire `paper_account_state.equity` depuis la DB. Ajouter un fallback sur le starting balance si aucun snapshot n'existe.

4. **Fixer le `pnl_pct`** : il devrait être le rendement sur l'equity, pas sur le prix :
   ```python
   pnl_pct = pnl_usd / equity_at_trade_open
   ```
   Ou a minima, le daily loss gate doit comparer `sum(pnl_usd) / equity`, pas `sum(pnl_pct)`.

### 🔴 Priorité 2 — Corriger le backtesting

5. **Ajouter un modèle de spread/slippage** dans `simulate_signal_mode_trade_outcome` : au minimum un spread fixe de $0.30 appliqué à l'entrée et aux sorties.

6. **Inclure le sizing dans le backtesting** : les P&L de l'optimizer devraient être pondérés par le sizing (même simplifié) pour que les métriques soient comparables au live.

7. **Supprimer le code mort** : `simulate_trade_outcome` et tout code vestige.

### 🟠 Priorité 3 — Robustesse opérationnelle

8. **Ajouter un TTL sur les trades OPEN** : fermer automatiquement après 48-72h sans résolution avec `close_reason="EXPIRED"`.

9. **Ajouter un kill switch** : endpoint POST `/api/kill` qui set un flag Redis `risk:kill_switch=1`, vérifié par le pipeline avant de générer des signaux.

10. **Ajouter un equity drawdown circuit breaker** : si l'equity tombe sous `starting_balance × 0.90`, arrêter tout trading.

11. **Unifier l'implémentation ATR** : une seule fonction partagée, avec un choix explicite Wilder vs SMA.

12. **Migrer vers des données XAUUSD réelles** (Dukascopy .bi5 déjà validé par AGENTS.md) pour tout backtesting/optimization, et garder PAXG uniquement pour la plomberie live.

---

## 14. Statut de remédiation — 2026-05-25

Les corrections ont été appliquées dans le plan `docs/superpowers/plans/2026-05-23-structural-audit-remediation.md`.
Les commits sont différés car la branche contient plusieurs lots de corrections imbriqués et des fichiers non suivis préexistants.

| Finding | Statut | Remédiation |
|---|---|---|
| F-01 | Corrigé | Task 1: `src/market/instruments.py` centralise `InstrumentSpec` et le contract size XAUUSD. |
| F-02 | Corrigé | Task 3: `RiskGateRunner` vérifie l'exposition notionnelle post-trade avant approbation. |
| F-03 | Corrigé | Tasks 3-5: `pnl_pct` est un rendement compte, pas un rendement prix. |
| F-04 | Corrigé | Task 3: sizing basé sur l'equity paper dynamique via `src/risk/account.py`. |
| F-05 | Corrigé | Task 5/6: expiry théorique et `EXPIRED` empêchent les trades OPEN indéfinis. |
| F-06 | Corrigé | Task 6: optimizer/backtest utilisent sizing, gates, equity rolling et coûts. |
| F-07 | Corrigé | Task 6: backtest et monitoring utilisent les mêmes unités de rendement compte. |
| F-08 | Corrigé | Task 7: proxy Binance/PAXG marqué `runtime_proxy`; optimizer exige source research Dukascopy. |
| F-09 | Corrigé | Task 6: spread/slippage appliqués via `src/backtesting/execution_costs.py`. |
| F-10 | Corrigé | Task 6: scoring robuste avec validation OOS multi-fenêtres et pénalité de voisinage. |
| F-11 | Corrigé | Task 8: gate `max_equity_drawdown` avant sizing. |
| F-12 | Corrigé | Task 3/8: concentration réduit puis bloque selon seuils configurés. |
| F-13 | Corrigé | Task 8: kill switch Redis + `POST /api/kill` / `POST /api/resume`. |
| F-14 | Corrigé | Task 3: `exposure_multiple_after` devient une gate effective. |
| F-15 | Corrigé | Tasks 4-6: `TradeORM.pnl` USD et `TradeORM.pnl_pct` compte sont cohérents. |
| F-16 | Corrigé | Task 8: drawdown breaker equity dans `RiskGateRunner`. |
| F-17 | Corrigé | Tasks 5/8: breaker bloque les nouveaux trades; les positions ouvertes restent gérées/expirées selon la spec. |
| F-18 | Corrigé localement | Task 10: clés S3 retirées du `.env` local; `.env.example` reste sans secret. |
| F-19 | Corrigé | Task 8: token optionnel `X-Dashboard-Token` pour endpoints opérateur mutatifs. |
| F-20 | Corrigé | Task 8: rate limiter Redis fixed-window sur `/health`, `/dashboard`, `/api/dashboard`. |
| F-21 | Corrigé | Task 5: monitor trade lifecycle dans une session contrôlée. |
| F-22 | Corrigé | Task 5: guard temporel et session ownership renforcés dans le monitoring. |
| F-23 | Corrigé | Task 9: ATR partagé dans `src/indicators/atr.py`. |
| F-24 | Corrigé | Task 6: ancien simulateur mort supprimé; simulation signal-mode unique. |
| F-25 | Corrigé | Task 9: `validate_params_against_ranges()` bloque les params hors bornes. |
| F-26 | Corrigé | Task 9: `optimizer_result_count` initialisé avant la branche query. |
| F-27 | Corrigé | Task 10: engine SQLAlchemy créé lazy via `get_engine()`, plus au simple import. |
| F-28 | Corrigé | Task 9: EMA partagé dans `src/indicators/ema.py`. |

### Vérifications principales

```bash
./.venv/bin/pytest tests/test_strategies tests/test_pipeline/test_regime_detector.py -v
# 95 passed

./.venv/bin/pytest tests/test_monitoring/test_health_risk.py tests/test_monitoring/test_dashboard.py tests/test_risk/test_runner.py -v
# 49 passed

./.venv/bin/pytest -q
# 379 passed
```
