# Requirements: 0rum

**Defined:** 2026-04-05
**Core Value:** The bot must reliably generate validated XAUUSD signals in mode signal, with every trade candidate passing all risk gates — signal quality and capital protection are non-negotiable before any auto-execution is considered.

## v1 Requirements

### Infrastructure

- [ ] **INFRA-01**: Docker Compose stack (postgres, redis, app) starts cleanly and `/health` responds
- [ ] **INFRA-02**: Structured JSON logging via structlog throughout all components

### Data Ingestion

- [ ] **DATA-01**: XAUUSD-aligned candle ingestion for M15, H1, H4, D1 timeframes with auto-backfill on startup
- [ ] **DATA-02**: Gap detection flags missing candle windows before strategy execution

### Strategy Engines

- [x] **STRAT-01**: Liquidity Sweep strategy generates CandidateSignals with direction, entry, SL, TP1, TP2
- [x] **STRAT-02**: Trend Continuation strategy generates CandidateSignals
- [x] **STRAT-03**: Breakout Expansion strategy generates CandidateSignals
- [x] **STRAT-04**: EMA Momentum strategy generates CandidateSignals
- [x] **STRAT-05**: Each strategy uses at most 3 optimizable parameters

### Signal Pipeline

- [ ] **PIPE-01**: Deduplication filter removes duplicate signals for same instrument/direction within cooldown window
- [ ] **PIPE-02**: Conflict filter removes opposing signals when a position is already theoretical-open
- [ ] **PIPE-03**: Signal ranker scores and orders candidates using confidence + regime fit
- [ ] **PIPE-04**: Quota gate enforces maximum concurrent approved signals
- [ ] **PIPE-05**: Market regime detection classifies TRENDING_UP, TRENDING_DOWN, RANGING, HIGH_VOL and feeds signal ranking

### Optimization

- [x] **OPTIM-01**: Walk-forward optimizer samples 100 parameter combos via Latin Hypercube Sampling per strategy
- [x] **OPTIM-02**: WFE > 50% gate — strategies with failing walk-forward efficiency are not activated
- [x] **OPTIM-03**: Multi-window test requires 2 of 3 OOS windows profitable before activating params
- [x] **OPTIM-04**: Optimizer runs every 24h and activates best-validated params for each strategy
- [x] **OPTIM-05**: Monte Carlo validation (1000 simulations) — P95 drawdown ≤ 2× historical, P5 profit factor > 1.0

### Risk Management

- [ ] **RISK-01**: Daily loss limit gate blocks all new signals if cumulative theoretical loss ≥ −3%
- [ ] **RISK-02**: Max positions gate blocks new signals if concurrent theoretical positions ≥ 5
- [x] **RISK-03**: Concentration check gate blocks signals that would over-expose the same direction
- [x] **RISK-04**: ATR-based position sizing with volatility adjustment and 2% hard cap per trade
- [ ] **RISK-05**: Circuit breaker triggers 24h trading shutdown after 8 consecutive stop-losses and surfaces the state locally

### Signal Mode

- [ ] **SIG-01**: Mode `signal` persists approved signal details with entry, SL, TP1, TP2, confidence
- [ ] **SIG-02**: Theoretical trade lifecycle (open → TP1 hit → trailing → close) tracked in PostgreSQL
- [ ] **SIG-03**: Theoretical P&L and stats (win rate, profit factor) accumulated per strategy in DB

### Auto Mode

- [ ] **AUTO-01**: Mode `auto` places market/limit orders on the selected execution broker for approved signals
- [ ] **AUTO-02**: Partial close at TP1 (50% position) with ATR trailing stop activated on remainder
- [ ] **AUTO-03**: Stop-loss and take-profit orders managed via the selected broker API

### Monitoring

- [ ] **NOTIF-01**: Local dashboard/API exposes new approved signals
- [ ] **NOTIF-02**: Local dashboard/API exposes TP1 hit, TP2 hit, and SL hit lifecycle state
- [ ] **NOTIF-03**: Local dashboard/API exposes circuit breaker state
- [ ] **NOTIF-04**: Daily summary state is reconstructable from local database records

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
| External data APIs | No external dependencies beyond the selected market-data/execution providers |
| Multi-asset | XAUUSD exclusively — instrument loop out of scope |
| OAuth/user management | Single-operator bot — no auth system needed |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| INFRA-01 | Phase 1 | Pending |
| INFRA-02 | Phase 1 | Pending |
| DATA-01 | Phase 2 | Pending |
| DATA-02 | Phase 2 | Pending |
| STRAT-01 | Phase 3 | Complete |
| STRAT-02 | Phase 3 | Complete |
| STRAT-03 | Phase 3 | Complete |
| STRAT-04 | Phase 3 | Complete |
| STRAT-05 | Phase 3 | Complete |
| PIPE-01 | Phase 4 | Pending |
| PIPE-02 | Phase 4 | Pending |
| PIPE-03 | Phase 4 | Pending |
| PIPE-04 | Phase 4 | Pending |
| PIPE-05 | Phase 4 | Pending |
| OPTIM-01 | Phase 5 | Complete |
| OPTIM-02 | Phase 5 | Complete |
| OPTIM-03 | Phase 5 | Complete |
| OPTIM-04 | Phase 5 | Complete |
| OPTIM-05 | Phase 5 | Complete |
| RISK-01 | Phase 6 | Pending |
| RISK-02 | Phase 6 | Pending |
| RISK-03 | Phase 6 | Complete |
| RISK-04 | Phase 6 | Complete |
| RISK-05 | Phase 6 | Pending |
| SIG-01 | Phase 7 | Pending |
| SIG-02 | Phase 7 | Pending |
| SIG-03 | Phase 7 | Pending |
| NOTIF-01 | Phase 7 | Pending |
| NOTIF-02 | Phase 7 | Pending |
| NOTIF-03 | Phase 7 | Pending |
| NOTIF-04 | Phase 7 | Pending |
| AUTO-01 | Phase 8 | Pending |
| AUTO-02 | Phase 8 | Pending |
| AUTO-03 | Phase 8 | Pending |

**Coverage:**
- v1 requirements: 34 total
- Mapped to phases: 34
- Unmapped: 0 ✓

---
*Requirements defined: 2026-04-05*
*Last updated: 2026-04-26 — Phase 5 optimizer validation complete*
