# 0rum — AGENTS.md v2

> **Spec technique complète du trading bot autonome XAUUSD.**
> Ce fichier est la source unique de vérité. Codex doit pouvoir construire le système entier à partir de ce document seul.

## Provider Truth Override — 2026-05-20

Règles de priorité :
- la voie runtime actuelle reste le proxy `Binance/CCXT PAXG/USDT` uniquement pour la plomberie
- la source active research/backtest validée pour le vrai `XAUUSD` est **Dukascopy public `.bi5`**
- Dukascopy ne doit pas être traité comme provider live/runtime
- toute section plus bas qui présente un autre provider comme cible active doit être lue comme historique
- la séparation `market data provider` / `execution broker` reste obligatoire

## Monitoring Surface Override — 2026-05-20

L'ancienne surface opérateur de notification externe est abandonnée au profit d'une UI web locale.

Règles de priorité :
- `signal mode` ne dépend plus d'aucun canal externe de notification
- la surface active est `GET /dashboard` + `GET /api/dashboard`
- les signaux approuvés doivent être persistés, traçables localement, puis visibles dans la UI web
- toute section plus bas qui présente un canal externe comme surface active doit être lue comme historique

---

## 1. Identité du projet

| Clé | Valeur |
|---|---|
| Nom | **0rum** (prononcé « orum ») |
| Origine | 0 → lien avec 0xBot · rum → *aurum* (or en latin) |
| Asset unique | XAUUSD (Gold / US Dollar) |
| Provider cible | Runtime plumbing : Binance/CCXT `PAXG/USDT` proxy · Research/backtest : Dukascopy `.bi5` |
| Objectif | Bot de trading 24/7, signal-only puis auto-execution |
| Auteur | Non-développeur — tout le code est produit par Codex |

---

## 2. Stack technique

```
Python 3.12
FastAPI (API interne health/metrics)
PostgreSQL 16 (stockage candles, signaux, trades, optimizer)
Redis (cache, rate limiting, circuit breaker state)
Docker Compose (orchestration)
structlog (logging structuré JSON)
Pydantic v2 (validation, settings, data classes)
SQLAlchemy 2.0 (async, mapped_column)
httpx (clients broker/provider async)
ccxt (implémentation temporaire de proxy data en Phase 2)
scipy (argrelextrema pour swing detection)
numpy / pandas (calculs indicateurs)
jinja2 (dashboard web local)
```

### Déploiement

- Docker Compose : `app` (bot), `postgres`, `redis`
- Cible : VPS ou Mac Studio M3 Ultra local
- Surface opérateur : dashboard web local read-only

---

## 3. Architecture — 7 Layers

```
┌─────────────────────────────────────────────────────┐
│                    MONITORING (L7)                   │
│       Dashboard local · Health API · Alerting        │
├─────────────────────────────────────────────────────┤
│               EXECUTION ENGINE (L6)                  │
│ Mode signal (local/web) │ Mode auto (broker natif)   │
├─────────────────────────────────────────────────────┤
│               RISK MANAGEMENT (L5)                   │
│   Daily loss limit · Max positions · ATR sizing      │
│   Circuit breaker · Hard cap                         │
├─────────────────────────────────────────────────────┤
│        BACKTESTING & VALIDATION (L4)                 │
│   Walk-Forward · Monte Carlo · Regime Detection      │
│   LHS Optimizer · WFE scoring                        │
├─────────────────────────────────────────────────────┤
│              SIGNAL PIPELINE (L3)                    │
│   Dedup · Conflict filter · Ranking · Quota          │
├─────────────────────────────────────────────────────┤
│             STRATEGY ENGINE (L2)                     │
│   4 stratégies parallèles → CandidateSignals         │
├─────────────────────────────────────────────────────┤
│             DATA INGESTION (L1)                      │
│ Provider feed · 4 timeframes · PostgreSQL            │
└─────────────────────────────────────────────────────┘
```

---

## 4. Structure du projet

```
0rum/
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── pyproject.toml
├── alembic/
│   ├── alembic.ini
│   ├── env.py
│   └── versions/
├── src/
│   ├── __init__.py
│   ├── main.py                    # FastAPI app + startup lifecycle
│   ├── config.py                  # Pydantic Settings (tout depuis .env)
│   ├── database.py                # Async engine, sessionmaker
│   ├── models/
│   │   ├── __init__.py
│   │   ├── candle.py              # Candle ORM
│   │   ├── signal.py              # CandidateSignal, ApprovedSignal ORM
│   │   ├── trade.py               # Trade ORM
│   │   ├── optimizer_result.py    # OptimizerResult ORM
│   │   └── regime.py              # MarketRegime ORM
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── market_client.py       # Market data provider client
│   │   ├── candle_fetcher.py      # Fetch + store candles
│   │   └── gap_detector.py        # Gap detection + backfill
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── base.py                # AbstractStrategy
│   │   ├── liquidity_sweep.py     # Strategy 1
│   │   ├── trend_continuation.py  # Strategy 2
│   │   ├── breakout_expansion.py  # Strategy 3
│   │   └── ema_momentum.py        # Strategy 4
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── dedup.py
│   │   ├── conflict_filter.py
│   │   ├── ranker.py
│   │   └── quota.py
│   ├── backtesting/
│   │   ├── __init__.py
│   │   ├── walk_forward.py        # Walk-forward engine
│   │   ├── monte_carlo.py         # Monte Carlo validation
│   │   ├── regime_detector.py     # Market regime detection
│   │   └── optimizer.py           # LHS optimizer
│   ├── risk/
│   │   ├── __init__.py
│   │   ├── position_sizer.py      # ATR-based sizing
│   │   ├── risk_gates.py          # 3 gates pre-trade
│   │   └── circuit_breaker.py     # Consecutive stops → shutdown
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── executor.py            # Mode router (signal vs auto)
│   │   └── broker_executor.py     # Broker order placement
│   ├── monitoring/
│   │   ├── __init__.py
│   │   ├── dashboard.py           # /dashboard + /api/dashboard
│   │   └── health.py              # /health endpoint
│   ├── templates/
│   │   └── dashboard.html         # UI locale read-only
│   └── scheduler/
│       ├── __init__.py
│       └── jobs.py                # APScheduler tasks
└── tests/
    ├── conftest.py
    ├── test_strategies/
    ├── test_pipeline/
    ├── test_backtesting/
    ├── test_risk/
    └── test_execution/
```

---

## 5. Configuration — `.env`

```env
# === MARKET DATA PROVIDER ===
MARKET_DATA_PROVIDER=binance

# === DATABASE ===
DATABASE_URL=postgresql+asyncpg://orum:orum@postgres:5432/orum
REDIS_URL=redis://redis:6379/0

# === EXECUTION MODE ===
EXECUTION_MODE=signal  # "signal" ou "auto"

# === RISK (TOUS FIXÉS) ===
RISK_PER_TRADE=0.01
DAILY_LOSS_LIMIT=-0.03
MAX_POSITIONS=5
MAX_SIGNALS_PER_DAY=5
CIRCUIT_BREAKER_STOPS=8
CIRCUIT_BREAKER_COOLDOWN_HOURS=24
ATR_HIGH_VOL_PERCENTILE=90
ATR_LOW_VOL_PERCENTILE=10
HARD_CAP_RISK=0.02

# === OPTIMIZER ===
OPTIMIZER_INTERVAL_HOURS=24
BACKTEST_INTERVAL_HOURS=8
WF_TRAIN_MONTHS=6
WF_TEST_MONTHS=2
LHS_COMBOS=100
WFE_MINIMUM=0.50
```

### Pydantic Settings

```python
from pydantic_settings import BaseSettings
from enum import Enum

class ExecutionMode(str, Enum):
    SIGNAL = "signal"
    AUTO = "auto"

class Settings(BaseSettings):
    market_data_provider: str = "binance"

    # Database
    database_url: str
    redis_url: str = "redis://redis:6379/0"

    # Execution
    execution_mode: ExecutionMode = ExecutionMode.SIGNAL

    # Risk (TOUS FIXÉS — pas dans l'optimizer)
    risk_per_trade: float = 0.01
    daily_loss_limit: float = -0.03
    max_positions: int = 5
    max_signals_per_day: int = 5
    circuit_breaker_stops: int = 8
    circuit_breaker_cooldown_hours: int = 24
    atr_high_vol_percentile: int = 90
    atr_low_vol_percentile: int = 10
    hard_cap_risk: float = 0.02

    # Optimizer
    optimizer_interval_hours: int = 24
    backtest_interval_hours: int = 8
    wf_train_months: int = 6
    wf_test_months: int = 2
    lhs_combos: int = 100
    wfe_minimum: float = 0.50

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
```

---

## 6. Schéma SQL

### Table `candles`

```sql
CREATE TABLE candles (
    id          BIGSERIAL PRIMARY KEY,
    instrument  VARCHAR(10) NOT NULL DEFAULT 'XAUUSD',
    timeframe   VARCHAR(5)  NOT NULL,  -- M15, H1, H4, D1
    timestamp   TIMESTAMPTZ NOT NULL,
    open        NUMERIC(12,5) NOT NULL,
    high        NUMERIC(12,5) NOT NULL,
    low         NUMERIC(12,5) NOT NULL,
    close       NUMERIC(12,5) NOT NULL,
    volume      INTEGER NOT NULL,
    complete    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (instrument, timeframe, timestamp)
);

CREATE INDEX idx_candles_tf_ts ON candles (timeframe, timestamp DESC);
CREATE INDEX idx_candles_instrument_tf ON candles (instrument, timeframe);
```

### Table `candidate_signals`

```sql
CREATE TABLE candidate_signals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy        VARCHAR(30) NOT NULL,  -- liquidity_sweep, trend_continuation, breakout_expansion, ema_momentum
    direction       VARCHAR(5)  NOT NULL,  -- BUY, SELL
    entry_price     NUMERIC(12,5) NOT NULL,
    sl_price        NUMERIC(12,5) NOT NULL,
    tp1_price       NUMERIC(12,5) NOT NULL,
    tp2_price       NUMERIC(12,5),
    confidence      NUMERIC(4,3) NOT NULL,  -- 0.000 - 1.000
    timeframe       VARCHAR(5) NOT NULL,
    params_snapshot JSONB NOT NULL,          -- snapshot des params utilisés
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status          VARCHAR(15) NOT NULL DEFAULT 'PENDING'  -- PENDING, APPROVED, REJECTED, DEDUPED
);

CREATE INDEX idx_candidate_created ON candidate_signals (created_at DESC);
CREATE INDEX idx_candidate_status ON candidate_signals (status);
```

### Table `approved_signals`

```sql
CREATE TABLE approved_signals (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_signal_id UUID NOT NULL REFERENCES candidate_signals(id),
    rank_score          NUMERIC(6,4) NOT NULL,
    risk_check_passed   BOOLEAN NOT NULL DEFAULT TRUE,
    execution_status    VARCHAR(15) NOT NULL DEFAULT 'PENDING',  -- PENDING, SENT, EXECUTED, SKIPPED
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### Table `trades`

```sql
CREATE TABLE trades (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    approved_signal_id  UUID NOT NULL REFERENCES approved_signals(id),
    broker_trade_id     VARCHAR(30),
    direction           VARCHAR(5) NOT NULL,
    entry_price         NUMERIC(12,5) NOT NULL,
    sl_price            NUMERIC(12,5) NOT NULL,
    tp1_price           NUMERIC(12,5) NOT NULL,
    tp2_price           NUMERIC(12,5),
    size_lots           NUMERIC(8,4) NOT NULL,
    status              VARCHAR(15) NOT NULL DEFAULT 'OPEN',  -- OPEN, TP1_HIT, CLOSED, STOPPED
    pnl                 NUMERIC(12,5),
    pnl_pct             NUMERIC(8,5),
    opened_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at           TIMESTAMPTZ,
    close_reason        VARCHAR(20)  -- TP1, TP2, SL, TRAIL, MANUAL, CIRCUIT_BREAKER
);

CREATE INDEX idx_trades_status ON trades (status);
CREATE INDEX idx_trades_opened ON trades (opened_at DESC);
```

### Table `optimizer_results`

```sql
CREATE TABLE optimizer_results (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy        VARCHAR(30) NOT NULL,
    params          JSONB NOT NULL,
    train_start     TIMESTAMPTZ NOT NULL,
    train_end       TIMESTAMPTZ NOT NULL,
    test_start      TIMESTAMPTZ NOT NULL,
    test_end        TIMESTAMPTZ NOT NULL,
    in_sample_score NUMERIC(8,5) NOT NULL,
    oos_score       NUMERIC(8,5) NOT NULL,
    wfe             NUMERIC(6,4) NOT NULL,  -- walk-forward efficiency
    profit_factor   NUMERIC(8,4),
    sharpe_ratio    NUMERIC(8,4),
    win_rate        NUMERIC(6,4),
    max_drawdown    NUMERIC(8,5),
    trade_count     INTEGER NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_optimizer_strategy ON optimizer_results (strategy, created_at DESC);
CREATE INDEX idx_optimizer_active ON optimizer_results (is_active) WHERE is_active = TRUE;
```

### Table `market_regimes`

```sql
CREATE TABLE market_regimes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp   TIMESTAMPTZ NOT NULL,
    regime      VARCHAR(15) NOT NULL,  -- TRENDING_UP, TRENDING_DOWN, RANGING, HIGH_VOL
    atr_value   NUMERIC(12,5) NOT NULL,
    atr_pctile  NUMERIC(6,4) NOT NULL,
    adx_value   NUMERIC(8,4),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_regime_ts ON market_regimes (timestamp DESC);
```

---

## 7. Data Classes Pydantic

```python
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum
from uuid import UUID
from typing import Optional

# === Enums ===

class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class Timeframe(str, Enum):
    M15 = "M15"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"

class StrategyName(str, Enum):
    LIQUIDITY_SWEEP = "liquidity_sweep"
    TREND_CONTINUATION = "trend_continuation"
    BREAKOUT_EXPANSION = "breakout_expansion"
    EMA_MOMENTUM = "ema_momentum"

class SignalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DEDUPED = "DEDUPED"

class TradeStatus(str, Enum):
    OPEN = "OPEN"
    TP1_HIT = "TP1_HIT"
    CLOSED = "CLOSED"
    STOPPED = "STOPPED"

class MarketRegimeType(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    HIGH_VOL = "HIGH_VOL"

# === Data Classes ===

class CandleData(BaseModel):
    instrument: str = "XAUUSD"
    timeframe: Timeframe
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    complete: bool = True

class CandidateSignal(BaseModel):
    strategy: StrategyName
    direction: Direction
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: Optional[float] = None
    confidence: float = Field(ge=0.0, le=1.0)
    timeframe: Timeframe
    params_snapshot: dict

class ApprovedSignal(BaseModel):
    candidate_signal_id: UUID
    rank_score: float
    risk_check_passed: bool = True

class TradeRecord(BaseModel):
    approved_signal_id: UUID
    direction: Direction
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: Optional[float] = None
    size_lots: float
    status: TradeStatus = TradeStatus.OPEN

class StrategyParams(BaseModel):
    """Paramètres optimisables d'une stratégie — max 3 par stratégie."""
    strategy: StrategyName
    params: dict  # Ex: {"sweep_atr_mult": 0.5, "sl_atr_mult": 0.6, "tp_risk_mult": 2.0}
    wfe: float = 0.0
    is_active: bool = False

class MarketRegime(BaseModel):
    timestamp: datetime
    regime: MarketRegimeType
    atr_value: float
    atr_pctile: float
    adx_value: Optional[float] = None
```

---

## 8. Layer 1 — Data Ingestion

### 8.1 Market Data Client

```python
# src/ingestion/market_client.py
from src.config import Settings

class MarketDataClient:
    """Client async pour le provider de marché XAUUSD retenu."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def get_candles(
        self,
        instrument: str,
        granularity: str,  # M15, H1, H4, D1
        count: int = 500,
        from_time: str | None = None,
    ) -> list[dict]:
        """Fetch normalized candles from the selected provider."""
        ...
```

- Runtime actuel : `market_client.py` avec proxy `Binance/CCXT PAXG/USDT`
- Research/backtest actuel : Dukascopy public `.bi5` validé pour `XAUUSD`
- Règle projet : le proxy Binance/PAXG reste acceptable pour la plomberie, pas pour la validation stratégique

### 8.2 Candle Fetcher

- Fetch les 4 timeframes : **M15, H1, H4, D1**
- Stocke dans `candles` avec upsert (ON CONFLICT DO NOTHING)
- Au startup : backfill automatique (6 mois de données)
- Auto-refresh cyclique :
  - M15 → toutes les 15 minutes
  - H1 → toutes les heures
  - H4 → toutes les 4 heures
  - D1 → tous les jours à 00:05 UTC

### 8.3 Gap Detection

- Après chaque fetch, scanner les trous temporels
- Si gap détecté → backfill automatique
- Pruning : supprimer les candles `complete=False` > 24h
- Log chaque gap détecté et comblé via structlog

---

## 9. Layer 2 — Strategy Engine

### 9.1 Abstract Base

```python
# src/strategies/base.py
from abc import ABC, abstractmethod
from src.models.signal import CandidateSignal

class AbstractStrategy(ABC):
    """Chaque stratégie implémente generate_signals()."""

    # Chaque stratégie déclare ses paramètres optimisables
    PARAM_RANGES: dict[str, tuple[float, float]] = {}

    def __init__(self, params: dict):
        self.params = params

    @abstractmethod
    async def generate_signals(self, candles: dict) -> list[CandidateSignal]:
        """
        Args:
            candles: dict keyed by timeframe → list of CandleData
                     {"M15": [...], "H1": [...], "H4": [...], "D1": [...]}
        Returns:
            List of CandidateSignal
        """
        ...

    def calculate_atr(self, candles: list, period: int = 14) -> float:
        """ATR(14) helper — utilisé par toutes les stratégies."""
        ...

    def detect_swing_levels(self, candles: list, order: int = 10) -> tuple[list, list]:
        """Swing highs/lows via scipy.signal.argrelextrema. order=10 FIXÉ."""
        ...
```

### 9.2 Stratégie 1 : Liquidity Sweep

**Concept** : Détecter les faux breakouts aux niveaux S/R clés — le prix sweep la liquidité puis reverse.

**Paramètres optimisables (3 max)** :

| Paramètre | Description | Range |
|---|---|---|
| `sweep_atr_mult` | Profondeur minimum du sweep (filtre bruit vs vrais sweeps) | 0.2 – 0.8 |
| `sl_atr_mult` | Distance SL = agressivité du trade | 0.3 – 1.0 |
| `tp_risk_mult` | Ratio reward/risk (TP1 en multiples du risque) | 1.2 – 3.0 |

**Paramètres FIXÉS (jamais optimisés)** :
- Swing detection : `order=10` sur H4 (argrelextrema)
- TP2 = 2 × TP1 (ratio fixe)
- Pas de filtre volume

**Logique complète** :
1. Identifier swing highs/lows sur H4/D1 via `argrelextrema(order=10)` → niveaux S/R
2. Sur M15/H1 : détecter un wick qui perce le niveau de > `sweep_atr_mult × ATR(14)` mais dont le close revient à l'intérieur
3. Entry : première bougie M15 qui close back au-dessus (sweep de support) ou au-dessous (sweep de résistance) du niveau sweeped
4. SL : au-delà du sweep wick + `sl_atr_mult × ATR(14)`
5. TP1 : `tp_risk_mult × risk` (distance entry→SL)
6. TP2 : `2 × TP1`
7. Confidence = f(sweep depth, volume spike, proximity to level)

### 9.3 Stratégie 2 : Trend Continuation

**Concept** : Entrer sur pullbacks dans des trends établis.

**Paramètres optimisables (3 max)** :

| Paramètre | Description | Range |
|---|---|---|
| `pullback_ema` | Période de l'EMA servant de zone de pullback | 15 – 55 |
| `sl_atr_mult` | Distance SL sous le pullback low | 0.3 – 1.0 |
| `tp_risk_mult` | Ratio reward/risk | 1.2 – 3.0 |

**Paramètres FIXÉS** :
- Trend filter : `EMA(50) > EMA(200)` sur H1 = uptrend (inverse pour downtrend)
- Entry trigger = price action (engulfing, pin bar, inside bar breakout) — pas un paramètre

**Logique complète** :
1. Trend : `EMA(50) > EMA(200)` sur H1 → uptrend / inverse → downtrend
2. Pullback : prix retrace vers `EMA(pullback_ema)` sur H1 (touch ou pénétration légère)
3. Entry trigger : confirmation M15 price action — engulfing haussier, pin bar, inside bar breakout dans le sens du trend
4. SL : sous le pullback low − `sl_atr_mult × ATR(14)`
5. TP1 : `tp_risk_mult × risk`
6. TP2 : retest du swing high précédent
7. Confidence = f(trend strength via ADX, pullback quality, price action pattern)

### 9.4 Stratégie 3 : Breakout Expansion

**Concept** : Capturer les mouvements explosifs issus de range breakouts.

**Paramètres optimisables (3 max)** :

| Paramètre | Description | Range |
|---|---|---|
| `squeeze_lookback` | Durée minimum de consolidation (bougies H4) | 12 – 30 |
| `volume_mult` | Seuil de confirmation volume au breakout | 1.2 – 2.5 |
| `sl_atr_mult` | SL = profondeur dans le range | 0.5 – 1.5 |

**Paramètres FIXÉS** :
- TP = 1× range width (logique naturelle de breakout)
- Pas de RSI

**Logique complète** :
1. Range : identifier une consolidation H4 où range < `1.5 × ATR(14)` sur `squeeze_lookback` bougies
2. Breakout : H1 close à l'extérieur du range avec volume > `volume_mult × SMA_volume(20)`
3. Entry : sur retest du niveau cassé (si dans les 4 prochaines H1), sinon au breakout close
4. SL : mid-range (ajusté par `sl_atr_mult`)
5. TP : range width projeté depuis le point de breakout
6. Confidence = f(squeeze duration, volume ratio, range clarity)

### 9.5 Stratégie 4 : EMA Momentum

**Concept** : Entries propres sur EMA crossovers confirmés par le momentum.

**Paramètres optimisables (3 max)** :

| Paramètre | Description | Range |
|---|---|---|
| `fast_ema` | Période EMA rapide du crossover | 5 – 15 |
| `slow_ema` | Période EMA lente du crossover | 15 – 30 |
| `sl_atr_mult` | Distance SL | 0.2 – 0.8 |

**Paramètres FIXÉS** :
- TP = 1.5× risk (fixe, pas optimisé)
- Filtre trend : `EMA(fast_ema) > EMA(50)` pour les longs
- Pas de MACD (redondant avec les EMAs)

**Logique complète** :
1. Signal : `EMA(fast_ema)` croise `EMA(slow_ema)` sur H1
2. Filtre : `EMA(fast_ema)` au-dessus de `EMA(50)` pour longs (en dessous pour shorts)
3. Entry : au close de la bougie H1 du crossover
4. SL : côté opposé de `EMA(slow_ema)` − `sl_atr_mult × ATR(14)`
5. TP : `1.5 × risk` (distance entry→SL × 1.5)
6. Confidence = f(crossover angle, distance to EMA50, trend alignment)

---

## 10. Layer 3 — Signal Pipeline

Le pipeline traite les CandidateSignals en 4 étapes séquentielles :

### 10.1 Dedup

- Fenêtre : 60 minutes
- Même stratégie + même direction + entry price ±0.1% → dedup
- Le signal le plus récent gagne, les précédents → status `DEDUPED`

### 10.2 Conflict Filter

- Si un long ET un short existent pour le même instrument :
  - Garder celui avec la meilleure confidence
  - Rejeter l'autre → status `REJECTED`

### 10.3 Ranking

**Score composite** (même formule v1) :

```python
score = (
    confidence * 0.40 +
    risk_reward_ratio_normalized * 0.30 +
    strategy_recent_wfe * 0.20 +
    regime_alignment * 0.10
)
```

- `confidence` : confidance brute du signal (0–1)
- `risk_reward_ratio_normalized` : R:R normalisé entre 0 et 1 (cap à 4:1)
- `strategy_recent_wfe` : WFE du dernier optimizer run pour cette stratégie
- `regime_alignment` : 1.0 si la stratégie matche le régime actuel, 0.5 sinon

### 10.4 Quota

- Maximum `MAX_SIGNALS_PER_DAY` (5) approved par jour calendaire UTC
- Si quota atteint → signals supplémentaires → status `REJECTED` (reason: "quota")

---

## 11. Layer 4 — Backtesting & Validation

### 11.1 Walk-Forward Optimization

| Paramètre | Valeur |
|---|---|
| Train window | **6 mois** |
| Test window | **2 mois** |
| LHS combos | **100** par run |
| Fréquence | **24h** |
| Multi-window test | **3 fenêtres de 20 jours** |
| WFE minimum | **> 50%** (standard Pardo) |

**Process** :
1. Pour chaque stratégie, l'optimizer génère 100 combinaisons LHS (Latin Hypercube Sampling)
2. Chaque combinaison est backtestée sur la fenêtre d'entraînement (6 mois)
3. Les top 5 combinaisons (par score composite) sont testées sur la fenêtre de test OOS (2 mois)
4. Calcul du WFE : `WFE = OOS_score / IS_score`
5. Si `WFE > 0.50` → les paramètres sont activés
6. Si `WFE ≤ 0.50` → la stratégie conserve ses paramètres précédents (pas de rollback)
7. Les résultats sont stockés dans `optimizer_results`

**Multi-window validation** :
- Le test OOS (2 mois ≈ 60 jours) est découpé en 3 fenêtres de 20 jours
- Les paramètres doivent être profitables dans au moins 2/3 des fenêtres
- Un seul fenêtre perdante est toléré

### 11.2 Composite Score

```python
composite = (
    profit_factor  * 0.30 +
    sharpe_ratio   * 0.25 +
    win_rate       * 0.25 +
    (1 - max_dd)   * 0.20   # dd inversé : moins de drawdown = mieux
)
```

### 11.3 Monte Carlo Validation

- 1000 simulations par jeu de paramètres
- Shuffle de l'ordre des trades
- Vérifier que le P95 du drawdown ne dépasse pas 2× le drawdown historique
- Vérifier que le P5 du profit factor reste > 1.0

### 11.4 Regime Detection

- Régimes : `TRENDING_UP`, `TRENDING_DOWN`, `RANGING`, `HIGH_VOL`
- Basé sur ADX(14) + ATR percentile
- Stocké dans `market_regimes`
- Utilisé pour le `regime_alignment` dans le ranking
- Règles :
  - ADX > 25 + `EMA(50) > EMA(200)` → `TRENDING_UP`
  - ADX > 25 + `EMA(50) < EMA(200)` → `TRENDING_DOWN`
  - ADX ≤ 25 + ATR < 50th percentile → `RANGING`
  - ATR > 90th percentile → `HIGH_VOL` (override les autres)

---

## 12. Layer 5 — Risk Management

### 12.1 Les 3 Gates (pré-trade)

Chaque trade doit passer les 3 gates **avant** exécution :

**Gate 1 — Daily Loss Limit** :
- Calculer le P&L du jour (UTC)
- Si P&L ≤ `DAILY_LOSS_LIMIT` (-3%) → REJECT + circuit breaker alert

**Gate 2 — Position Limits** :
- Compter les trades `status=OPEN`
- Si count ≥ `MAX_POSITIONS` (5) → REJECT

**Gate 3 — Correlation/Concentration** :
- Puisque asset unique, cette gate vérifie que les positions ouvertes ne sont pas toutes dans la même direction
- Si 4+ positions dans la même direction → réduire la taille du nouveau trade de 50%

### 12.2 ATR-Based Position Sizing

```python
def calculate_position_size(
    equity: float,
    risk_per_trade: float,      # 0.01
    entry_price: float,
    sl_price: float,
    atr_value: float,
    atr_pctile: float,
    hard_cap: float = 0.02,
) -> float:
    risk_amount = equity * risk_per_trade  # ex: 10000 * 0.01 = 100$

    # Ajustement volatilité
    if atr_pctile >= 90:  # high vol
        risk_amount *= 0.7  # réduire 30%
    elif atr_pctile <= 10:  # low vol
        risk_amount *= 1.3  # augmenter 30% (cap)

    # Hard cap
    risk_amount = min(risk_amount, equity * hard_cap)

    sl_distance = abs(entry_price - sl_price)
    if sl_distance == 0:
        return 0.0

    # Pour XAUUSD : 1 lot = 100 oz, donc pip value dépend du prix
    lots = risk_amount / (sl_distance * 100)
    return round(lots, 2)
```

### 12.3 Circuit Breaker

- Compteur de stops consécutifs (stocké dans Redis)
- Si `CIRCUIT_BREAKER_STOPS` (8) stops consécutifs → shutdown 24h
- Pendant le shutdown :
  - Aucun nouveau trade accepté
  - Les positions ouvertes restent (pas de close forcé)
  - État visible immédiatement dans `/health` et `/api/dashboard`
  - Log structuré avec durée restante
- Reset du compteur au premier trade gagnant ou à la fin du cooldown

---

## 13. Layer 6 — Execution Modes

### 13.1 Mode `signal` (défaut)

Le pipeline tourne normalement jusqu'à l'ApprovedSignal. Au lieu d'exécuter chez un broker, le bot persiste le signal, l'expose via `/api/dashboard`, et l'affiche dans `/dashboard`.

- Le signal est loggé en DB avec `execution_status = 'SENT'`
- L'utilisateur décide manuellement d'exécuter ou non via le broker choisi
- Le bot continue à tracker le prix pour enregistrer le résultat théorique (win/loss)

**Tracking théorique** :
- Même si non exécuté, le bot surveille le prix post-signal
- Enregistre si le trade aurait atteint TP1, TP2, ou SL
- Permet de comparer hypothetical vs actual pendant la phase de validation

### 13.2 Mode `auto`

Le pipeline exécute automatiquement via l'API du broker choisi :

1. Placement d'un ordre limite ou market (selon la stratégie)
2. SL et TP1 attachés à l'ordre
3. À TP1 : partial close 50% de la position
4. Le reste : trailing stop basé sur ATR
5. Toutes les protections risk management actives

**Trailing après TP1** :
- Trail distance = `1.0 × ATR(14)` de la bougie H1 courante
- Mis à jour à chaque nouvelle bougie H1
- Ne recule jamais (ratchet only)

### 13.3 Transition signal → auto

- **Minimum 4 semaines en mode signal** avant de considérer le switch
- Critères de validation :
  - Les signaux envoyés doivent avoir un win rate > 55% (théorique)
  - Le profit factor théorique doit être > 1.3
  - Le WFE moyen des stratégies actives doit être > 50%
- Le switch se fait en changeant `EXECUTION_MODE=auto` dans `.env`
- Le changement de mode doit être visible dans `/health`, `/api/dashboard`, et les logs structurés

### 13.4 Router d'exécution

```python
# src/execution/executor.py
class ExecutionRouter:
    def __init__(self, settings: Settings, broker: BrokerExecutor):
        self.mode = settings.execution_mode
        self.broker = broker

    async def execute(self, signal: ApprovedSignal, size: float):
        if self.mode == ExecutionMode.SIGNAL:
            await self._record_signal_mode(signal, size)
            await self._start_theoretical_tracking(signal)
        elif self.mode == ExecutionMode.AUTO:
            await self.broker.place_order(signal, size)
```

---

## 14. Layer 7 — Monitoring

### 14.1 Dashboard local

Surface active :
- `GET /dashboard` — UI opérateur locale read-only
- `GET /api/dashboard` — JSON agrégé pour health, signaux, trades, P&L, circuit breaker, stratégies actives
- aucun canal externe requis pour le mode signal
- `📊 Daily Summary` — résumé quotidien à 00:00 UTC
- `🏥 Health Alert` — problème système

### 14.2 Health API

```
GET /health
```

```json
{
  "status": "healthy",
  "uptime_hours": 72.5,
  "execution_mode": "signal",
  "circuit_breaker": false,
  "open_positions": 2,
  "daily_pnl_pct": -0.45,
  "signals_today": 3,
  "last_candle_fetch": "2024-01-15T14:00:00Z",
  "strategies_active": 4,
  "redis_connected": true,
  "postgres_connected": true
}
```

### 14.3 Daily Summary (00:00 UTC)

```
📊 Daily Summary — 2024-01-15
━━━━━━━━━━━━━━━
Signals: 4 sent | 2 approved
Trades: 2 opened | 1 TP1 | 0 stopped
P&L: +1.2% (daily) | +4.8% (MTD)
Circuit Breaker: OFF (0/8 consecutive stops)
Mode: SIGNAL
━━━━━━━━━━━━━━━
```

---

## 15. Scheduler (APScheduler)

| Job | Intervalle | Description |
|---|---|---|
| `fetch_candles_m15` | 15 min | Fetch M15 candles |
| `fetch_candles_h1` | 1h | Fetch H1 candles |
| `fetch_candles_h4` | 4h | Fetch H4 candles |
| `fetch_candles_d1` | 1j (00:05 UTC) | Fetch D1 candles |
| `run_strategies` | 15 min | Exécuter les 4 stratégies |
| `process_pipeline` | 15 min (après strategies) | Pipeline de signaux |
| `detect_gaps` | 1h | Détection et backfill des gaps |
| `run_optimizer` | 24h | Walk-forward optimization |
| `run_backtest` | 8h | Backtesting validation |
| `detect_regime` | 4h | Market regime detection |
| `daily_summary` | 1j (00:00 UTC) | Résumé quotidien local/dashboard |
| `prune_candles` | 1j | Nettoyer les incomplètes > 24h |

---

## 16. Conventions de code

### MUST

- **Async everywhere** : toutes les fonctions I/O sont `async`
- **structlog** : logging structuré JSON, pas de `print()`
- **Pydantic v2** : validation des données, `model_validate` pas `from_orm`
- **SQLAlchemy 2.0** : `mapped_column`, pas de `Column()` legacy
- **Type hints** : partout, pas de `Any` sans justification
- **Docstrings** : Google style sur toutes les fonctions publiques
- **Error handling** : `try/except` spécifique, jamais de bare `except:`
- **Tests** : pytest + pytest-asyncio, fixtures avec `conftest.py`

### MUST NOT

- ❌ **Pas d'IA/ML** : pas de LSTM, pas de neural networks, pas de sklearn — les 4 stratégies sont purement techniques
- ❌ **Pas de multi-asset** : XAUUSD uniquement, jamais de boucle sur instruments
- ❌ **Pas de frontend externe** : la seule surface active est le dashboard web local
- ❌ **Pas de backtesting maison complexe** : utiliser le walk-forward + Monte Carlo décrits ici, pas de framework externe
- ❌ **Pas de paramètres magiques** : chaque nombre doit avoir une justification dans le doc
- ❌ **Pas d'overfitting** : 3 paramètres max par stratégie, WFE > 50% obligatoire
- ❌ **Pas de MACD** : redondant avec les EMAs dans la stratégie 4
- ❌ **Pas de RSI** : éliminé pour réduire le nombre de paramètres
- ❌ **Pas de dépendance API externe** hors provider de marché / broker retenus : pas de Fear & Greed index, pas de news API

---

## 17. Anti-Patterns à éviter

1. **Over-parameterization** : JAMAIS plus de 3 paramètres optimisables par stratégie. Si tu as envie d'en ajouter un, pose-toi la question : « est-ce que ce paramètre capture une dimension distincte du marché ? »
2. **Optimizing structural parameters** : SMA(200), EMA(50), swing order=10 sont STRUCTURELS. Ne les optimise jamais.
3. **Short train windows** : 3 mois = trop court pour H4. Minimum 6 mois.
4. **Ignoring WFE** : un backtest in-sample rentable ne veut RIEN dire sans WFE > 50%.
5. **Correlated parameters** : ne jamais avoir deux paramètres qui capturent la même chose (ex: fast_ema ET MACD fast period).
6. **Circular logic in confidence** : la confidence ne doit JAMAIS utiliser les données OOS ou les résultats actuels de l'optimizer.
7. **Silent failures** : chaque exception doit être loggée ET créer une notification si critique.
8. **Hardcoded credentials** : tout dans `.env`, jamais dans le code.

---

## 18. Build Order — 8 Phases

### Phase 1 : Foundation
- Docker Compose (postgres, redis, app)
- Settings, database engine, Alembic migrations
- Tous les modèles ORM
- Health endpoint
- **Livrable** : `docker compose up` fonctionne, `/health` répond

### Phase 2 : Data Ingestion
- Market data client (candles fetch)
- Candle fetcher avec upsert
- Gap detector + backfill
- Scheduler pour les 4 timeframes
- **Livrable** : Les candles s'accumulent en DB, pas de gap

### Phase 3 : Strategy Engine
- AbstractStrategy + 4 implémentations
- Indicateurs helpers (ATR, EMA, swing detection)
- Tests unitaires par stratégie avec données historiques gelées
- **Livrable** : Chaque stratégie peut générer des CandidateSignals

### Phase 4 : Signal Pipeline
- Dedup, conflict filter, ranker, quota
- Tests du pipeline complet
- **Livrable** : CandidateSignals → ApprovedSignals avec tous les filtres

### Phase 5 : Backtesting & Validation
- Walk-forward engine
- LHS optimizer
- Monte Carlo validation
- Regime detector
- **Livrable** : Les 4 stratégies ont des paramètres optimisés testés en OOS

### Phase 6 : Risk Management
- 3 risk gates
- Position sizer (ATR-based)
- Circuit breaker
- **Livrable** : Aucun trade ne passe sans passer les 3 gates

### Phase 7 : Execution Engine
- Mode signal : persistance locale + dashboard
- Mode auto : broker order executor + partial close + trailing stop
- Routing mode signal/auto
- Theoretical tracking (mode signal)
- **Livrable** : Bot fonctionnel en mode signal avec tracking

### Phase 8 : Monitoring & Polish
- Dashboard local complet
- Daily summary
- Health endpoint enrichi
- Logging structuré finalisé
- Docker production-ready
- **Livrable** : Bot prêt pour le mode signal en production

---

## 19. Provider Integration Notes

- La source research/backtest prioritaire pour le vrai `XAUUSD` est **Dukascopy public `.bi5`**
- `market data provider` et `execution broker` doivent rester découplés dans l'architecture
- Le proxy `Binance/CCXT PAXG/USDT` ne doit pas être utilisé pour valider la qualité stratégique en Phase 5+
- Toute future intégration broker doit préserver les timeframes `M15`, `H1`, `H4`, `D1`, le stockage uniforme dans `candles`, et l'abstraction `ExecutionRouter`

---

## 20. Docker Compose

```yaml
version: "3.8"

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: orum
      POSTGRES_PASSWORD: orum
      POSTGRES_DB: orum
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U orum"]
      interval: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 5

  app:
    build: .
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    ports:
      - "8000:8000"
    restart: unless-stopped

volumes:
  pgdata:
```

---

## 21. Résumé des paramètres

### Optimisables (par stratégie, max 3)

| Stratégie | Param 1 | Param 2 | Param 3 |
|---|---|---|---|
| Liquidity Sweep | `sweep_atr_mult` (0.2–0.8) | `sl_atr_mult` (0.3–1.0) | `tp_risk_mult` (1.2–3.0) |
| Trend Continuation | `pullback_ema` (15–55) | `sl_atr_mult` (0.3–1.0) | `tp_risk_mult` (1.2–3.0) |
| Breakout Expansion | `squeeze_lookback` (12–30) | `volume_mult` (1.2–2.5) | `sl_atr_mult` (0.5–1.5) |
| EMA Momentum | `fast_ema` (5–15) | `slow_ema` (15–30) | `sl_atr_mult` (0.2–0.8) |

### Globaux FIXÉS (jamais optimisés)

| Paramètre | Valeur |
|---|---|
| Max signaux/jour | 5 |
| Daily loss limit | −3% equity |
| Max positions | 5 |
| Risk per trade | 1% equity |
| Circuit breaker | 8 stops → 24h shutdown |
| ATR high vol threshold | 90th percentile |
| ATR low vol threshold | 10th percentile |
| Hard cap risk/trade | 2% |
| Swing order | 10 |
| Trend EMA fast | 50 |
| Trend EMA slow | 200 |
| EMA Momentum TP ratio | 1.5× risk |
| Breakout TP | 1× range width |
| Liquidity Sweep TP2 | 2× TP1 |

---

*Fin du AGENTS.md v2 — ce document est auto-suffisant pour construire l'intégralité du bot 0rum.*
