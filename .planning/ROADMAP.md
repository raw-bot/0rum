# Roadmap: 0rum

## Overview

0rum builds a 24/7 autonomous XAUUSD trading bot in eight natural delivery phases, each one unblocking the next. The critical path runs from infrastructure through data ingestion, strategies, signal pipeline, backtesting validation, and risk gates — all of which must be complete and proven before a single signal leaves the system. Signal mode ships first (Phase 7) so strategy quality can be validated for a minimum of four weeks against real market conditions before auto-execution is ever enabled (Phase 8).

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Foundation** - Docker Compose stack, database, ORM models, health endpoint
- [ ] **Phase 2: Data Ingestion** - OANDA candle fetching for 4 timeframes with backfill and gap detection
- [ ] **Phase 3: Strategy Engine** - 4 parallel technical strategies generating CandidateSignals
- [ ] **Phase 4: Signal Pipeline** - Dedup, conflict filter, regime detection, ranker, quota producing ApprovedSignals
- [ ] **Phase 5: Backtesting & Validation** - Walk-forward optimizer, Monte Carlo validation, LHS sampling
- [ ] **Phase 6: Risk Management** - 3 risk gates, ATR position sizing, circuit breaker
- [ ] **Phase 7: Signal Mode & Monitoring** - Telegram signal sending, theoretical trade tracking, full notifications
- [ ] **Phase 8: Auto Mode** - OANDA order execution, partial close at TP1, ATR trailing stop

## Phase Details

### Phase 1: Foundation
**Goal**: The project runs end-to-end in Docker with a live health endpoint and all data models in place
**Depends on**: Nothing (first phase)
**Requirements**: INFRA-01, INFRA-02
**Success Criteria** (what must be TRUE):
  1. `docker compose up` starts postgres, redis, and app without errors and all healthchecks pass
  2. `GET /health` returns a JSON response with `status: healthy` and connected service flags
  3. Alembic migrations run cleanly against a fresh postgres container and create all tables
  4. All structlog output is JSON-formatted — no plain `print()` calls exist anywhere in the codebase
**Plans**: 3 plans

Plans:
- [x] 01-01-PLAN.md — Project scaffold (docker-compose, Dockerfile, pyproject.toml, .env.example, src/config.py, alembic environment)
- [x] 01-02-PLAN.md — ORM models (6 tables) + async database engine + initial Alembic migration
- [x] 01-03-PLAN.md — FastAPI app, /health endpoint, structlog JSON configuration, human verification checkpoint

### Phase 2: Data Ingestion
**Goal**: XAUUSD candles for M15, H1, H4, D1 accumulate continuously in PostgreSQL with no gaps
**Depends on**: Phase 1
**Requirements**: DATA-01, DATA-02
**Success Criteria** (what must be TRUE):
  1. On first startup the bot automatically backfills 6 months of candles for all 4 timeframes
  2. The scheduler refreshes each timeframe on its correct cadence (M15 every 15 min, H1 every hour, H4 every 4h, D1 daily at 00:05 UTC)
  3. Any detected candle gap triggers an automatic backfill and is logged as a structured event
  4. After 24 hours of operation the `candles` table contains no gaps in any timeframe
**Plans**: 3 plans

Plans:
- [x] 02-01-PLAN.md — OANDA v20 async client + candle fetcher with upsert storage and 6-month backfill
- [x] 02-02-PLAN.md — Gap detector + APScheduler jobs (4 timeframes) + startup wiring in main.py
- [x] 02-03-PLAN.md — Ingestion unit tests (OandaClient, CandleFetcher, GapDetector — all mocked, no live deps)

### Phase 3: Strategy Engine
**Goal**: All four technical strategies can generate CandidateSignals with entry, SL, TP1, TP2, and confidence from live candle data
**Depends on**: Phase 2
**Requirements**: STRAT-01, STRAT-02, STRAT-03, STRAT-04, STRAT-05
**Success Criteria** (what must be TRUE):
  1. Each of the 4 strategies independently produces at least one CandidateSignal when fed a representative candle dataset
  2. No strategy declares more than 3 optimizable parameters in its `PARAM_RANGES` dict
  3. Every CandidateSignal contains a valid entry, SL, TP1, and a confidence score between 0 and 1
  4. Unit tests for each strategy pass against frozen historical candle fixtures (no live data dependency)
  5. Running all 4 strategies in parallel against the same candle set completes without errors
**Plans**: 4 plans

Plans:
- [ ] 03-01-PLAN.md — AbstractStrategy base (ATR, swing detection) + StrategyRunner (DB params, midpoint fallback, 500 candles, asyncio.gather)
- [ ] 03-02-PLAN.md — LiquiditySweepStrategy (STRAT-01) + TrendContinuationStrategy (STRAT-02) with linear confidence scoring
- [ ] 03-03-PLAN.md — BreakoutExpansionStrategy (STRAT-03) + EmaMomentumStrategy (STRAT-04) with linear confidence scoring
- [ ] 03-04-PLAN.md — Unit tests for all 4 strategies + StrategyRunner with frozen candle fixtures (no live DB)

### Phase 4: Signal Pipeline
**Goal**: Raw CandidateSignals are filtered, ranked, and gated into a ranked ApprovedSignal set with market regime context
**Depends on**: Phase 3
**Requirements**: PIPE-01, PIPE-02, PIPE-03, PIPE-04, PIPE-05
**Success Criteria** (what must be TRUE):
  1. Duplicate signals for the same instrument/direction within the 60-minute cooldown window are reduced to the highest-confidence survivor
  2. Conflicting long/short signals for the same instrument are resolved — only the higher-confidence direction passes
  3. The current market regime (TRENDING_UP, TRENDING_DOWN, RANGING, HIGH_VOL) is classified and stored before ranking runs
  4. ApprovedSignals are ranked by the composite score (confidence 40%, R:R 30%, WFE 20%, regime 10%)
  5. No more than `MAX_SIGNALS_PER_DAY` (5) ApprovedSignals are emitted in a single UTC calendar day
**Plans**: TBD

### Phase 5: Backtesting & Validation
**Goal**: Every strategy has walk-forward validated parameters with WFE > 50% before any signal can be generated from them
**Depends on**: Phase 2, Phase 3
**Requirements**: OPTIM-01, OPTIM-02, OPTIM-03, OPTIM-04, OPTIM-05
**Success Criteria** (what must be TRUE):
  1. The LHS optimizer generates exactly 100 parameter combinations per strategy and evaluates each against the 6-month training window
  2. Only parameter sets with WFE > 50% are written to `optimizer_results` with `is_active = TRUE`
  3. A strategy that fails the WFE gate retains its previous active parameters rather than being deactivated
  4. The multi-window test confirms parameters are profitable in at least 2 of 3 OOS windows before activation
  5. Monte Carlo validation (1000 simulations) confirms P95 drawdown ≤ 2× historical and P5 profit factor > 1.0
  6. The optimizer runs automatically every 24 hours via the APScheduler job
**Plans**: TBD

### Phase 6: Risk Management
**Goal**: No trade can execute without passing all three risk gates, position sizing is ATR-adjusted, and a circuit breaker halts trading after 8 consecutive stops
**Depends on**: Phase 2, Phase 4
**Requirements**: RISK-01, RISK-02, RISK-03, RISK-04, RISK-05
**Success Criteria** (what must be TRUE):
  1. A signal is blocked and logged as REJECTED when cumulative daily theoretical P&L is at or below -3%
  2. A signal is blocked and logged as REJECTED when 5 or more theoretical positions are already open
  3. A signal is blocked or its size reduced when 4+ open positions are in the same direction
  4. Position size is calculated using ATR-based sizing, reduced 30% in high-vol regimes and capped at 2% hard cap
  5. After 8 consecutive stop-losses the system enters a 24-hour shutdown, rejects all new signals, and sends a Telegram circuit breaker alert
**Plans**: TBD

### Phase 7: Signal Mode & Monitoring
**Goal**: The bot operates fully in signal mode — validated ApprovedSignals are sent to Telegram, theoretical trades are tracked, and all notification types fire correctly
**Depends on**: Phase 4, Phase 5, Phase 6
**Requirements**: SIG-01, SIG-02, SIG-03, NOTIF-01, NOTIF-02, NOTIF-03, NOTIF-04
**Success Criteria** (what must be TRUE):
  1. Every ApprovedSignal produces a correctly formatted Telegram signal message with entry, SL, TP1, TP2, confidence, and size suggestion
  2. The theoretical trade lifecycle (open → TP1 hit → trailing → close) is tracked in the `trades` table even though no real order is placed
  3. Per-strategy win rate, profit factor, and theoretical P&L are accumulated in the database and queryable
  4. TP1 hit, TP2 hit, SL hit, and circuit breaker events each produce the correct Telegram notification
  5. The daily summary Telegram message fires at 00:00 UTC with signals sent, trades, P&L, and circuit breaker state
**Plans**: TBD
**UI hint**: yes

### Phase 8: Auto Mode
**Goal**: The bot can execute real OANDA orders, manage partial closes at TP1, and trail stops — and is ready to be activated after the 4-week signal-mode validation period
**Depends on**: Phase 7
**Requirements**: AUTO-01, AUTO-02, AUTO-03
**Success Criteria** (what must be TRUE):
  1. In `EXECUTION_MODE=auto` the bot places a market or limit order on OANDA for each ApprovedSignal that passes all risk gates
  2. When price hits TP1 the system closes exactly 50% of the position via the OANDA partial-close endpoint
  3. After TP1 hit a trailing stop is activated on the remainder, trailing at 1.0× ATR(H1) and ratcheting only upward
  4. Switching from `signal` to `auto` in `.env` triggers a Telegram notification confirming the mode change
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 3/3 | Complete | 2026-04-05 |
| 2. Data Ingestion | 0/3 | Planned | - |
| 3. Strategy Engine | 0/4 | Planned | - |
| 4. Signal Pipeline | 0/? | Not started | - |
| 5. Backtesting & Validation | 0/? | Not started | - |
| 6. Risk Management | 0/? | Not started | - |
| 7. Signal Mode & Monitoring | 0/? | Not started | - |
| 8. Auto Mode | 0/? | Not started | - |
