# Requirements: 0rum

**Defined:** 2026-04-05
**Core Value:** The bot must reliably generate validated XAUUSD signals in mode signal, with every trade candidate passing all risk gates — signal quality and capital protection are non-negotiable before any auto-execution is considered.

## v1 Requirements

### Infrastructure

- [ ] **INFRA-01**: Docker Compose stack (postgres, redis, app) starts cleanly and `/health` responds
- [ ] **INFRA-02**: Structured JSON logging via structlog throughout all components

### Data Ingestion

- [ ] **DATA-01**: OANDA candle ingestion for M15, H1, H4, D1 timeframes with auto-backfill on startup
- [ ] **DATA-02**: Gap detection flags missing candle windows before strategy execution

### Strategy Engines

- [ ] **STRAT-01**: Liquidity Sweep strategy generates CandidateSignals with direction, entry, SL, TP1, TP2
- [ ] **STRAT-02**: Trend Continuation strategy generates CandidateSignals
- [ ] **STRAT-03**: Breakout Expansion strategy generates CandidateSignals
- [ ] **STRAT-04**: EMA Momentum strategy generates CandidateSignals
- [ ] **STRAT-05**: Each strategy uses at most 3 optimizable parameters

### Signal Pipeline

- [ ] **PIPE-01**: Deduplication filter removes duplicate signals for same instrument/direction within cooldown window
- [ ] **PIPE-02**: Conflict filter removes opposing signals when a position is already theoretical-open
- [ ] **PIPE-03**: Signal ranker scores and orders candidates using confidence + regime fit
- [ ] **PIPE-04**: Quota gate enforces maximum concurrent approved signals
- [ ] **PIPE-05**: Market regime detection classifies TRENDING_UP, TRENDING_DOWN, RANGING, HIGH_VOL and feeds signal ranking

### Optimization

- [ ] **OPTIM-01**: Walk-forward optimizer samples 100 parameter combos via Latin Hypercube Sampling per strategy
- [ ] **OPTIM-02**: WFE > 50% gate — strategies with failing walk-forward efficiency are not activated
- [ ] **OPTIM-03**: Multi-window test requires 2 of 3 OOS windows profitable before activating params
- [ ] **OPTIM-04**: Optimizer runs every 24h and activates best-validated params for each strategy
- [ ] **OPTIM-05**: Monte Carlo validation (1000 simulations) — P95 drawdown ≤ 2× historical, P5 profit factor > 1.0

### Risk Management

- [ ] **RISK-01**: Daily loss limit gate blocks all new signals if cumulative theoretical loss ≥ −3%
- [ ] **RISK-02**: Max positions gate blocks new signals if concurrent theoretical positions ≥ 5
- [ ] **RISK-03**: Concentration check gate blocks signals that would over-expose the same direction
- [ ] **RISK-04**: ATR-based position sizing with volatility adjustment and 2% hard cap per trade
- [ ] **RISK-05**: Circuit breaker triggers 24h trading shutdown after 8 consecutive stop-losses, sends Telegram alert

### Signal Mode

- [ ] **SIG-01**: Mode `signal` sends formatted Telegram signal message with entry, SL, TP1, TP2, confidence
- [ ] **SIG-02**: Theoretical trade lifecycle (open → TP1 hit → trailing → close) tracked in PostgreSQL
- [ ] **SIG-03**: Theoretical P&L and stats (win rate, profit factor) accumulated per strategy in DB

### Auto Mode

- [ ] **AUTO-01**: Mode `auto` places market/limit orders on OANDA for approved signals
- [ ] **AUTO-02**: Partial close at TP1 (50% position) with ATR trailing stop activated on remainder
- [ ] **AUTO-03**: Stop-loss and take-profit orders managed via OANDA v20 API

### Notifications

- [ ] **NOTIF-01**: Telegram notification on new approved signal (both modes)
- [ ] **NOTIF-02**: Telegram notification on TP1 hit, TP2 hit, SL hit
- [ ] **NOTIF-03**: Telegram notification on circuit breaker trigger
- [ ] **NOTIF-04**: Daily summary Telegram message with session stats

## v2 Requirements

### Operations

- **OPS-01**: Web dashboard for live P&L and strategy performance visualization
- **OPS-02**: Multi-asset support beyond XAUUSD
- **OPS-03**: External sentiment data (Fear & Greed, news) integration

## Out of Scope

| Feature | Reason |
|---------|--------|
| ML/neural networks (LSTM, sklearn) | Pure technical strategies only — AI in signal generation explicitly excluded to keep interpretability |
| RSI/MACD indicators | Explicitly eliminated to reduce parameter count and correlation |
| More than 3 optimizable params/strategy | Overfitting protection — hard constraint |
| Frontend/dashboard | `/health` endpoint sufficient for v1 |
| External data APIs | No external dependencies beyond OANDA v20 |
| Multi-asset | XAUUSD exclusively — instrument loop out of scope |
| OAuth/user management | Single-operator bot — no auth system needed |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| INFRA-01 | — | Pending |
| INFRA-02 | — | Pending |
| DATA-01 | — | Pending |
| DATA-02 | — | Pending |
| STRAT-01 | — | Pending |
| STRAT-02 | — | Pending |
| STRAT-03 | — | Pending |
| STRAT-04 | — | Pending |
| STRAT-05 | — | Pending |
| PIPE-01 | — | Pending |
| PIPE-02 | — | Pending |
| PIPE-03 | — | Pending |
| PIPE-04 | — | Pending |
| PIPE-05 | — | Pending |
| OPTIM-01 | — | Pending |
| OPTIM-02 | — | Pending |
| OPTIM-03 | — | Pending |
| OPTIM-04 | — | Pending |
| OPTIM-05 | — | Pending |
| RISK-01 | — | Pending |
| RISK-02 | — | Pending |
| RISK-03 | — | Pending |
| RISK-04 | — | Pending |
| RISK-05 | — | Pending |
| SIG-01 | — | Pending |
| SIG-02 | — | Pending |
| SIG-03 | — | Pending |
| AUTO-01 | — | Pending |
| AUTO-02 | — | Pending |
| AUTO-03 | — | Pending |
| NOTIF-01 | — | Pending |
| NOTIF-02 | — | Pending |
| NOTIF-03 | — | Pending |
| NOTIF-04 | — | Pending |

**Coverage:**
- v1 requirements: 34 total
- Mapped to phases: 0 (roadmapper will fill)
- Unmapped: 34 ⚠️

---
*Requirements defined: 2026-04-05*
*Last updated: 2026-04-05 after initial definition*
