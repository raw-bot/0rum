# 0rum Vision

This file captures the target product and architecture described in `AGENTS.md`, `LAUNCH_PROMPT.md`, `.planning/PROJECT.md`, and `.planning/ROADMAP.md`.

`CLAUDE.md` is the current-state repo memory.
This file is the intended end-state.

## Product target

- Build a 24/7 trading bot for one instrument: `XAUUSD`.
- Start in signal mode.
- Move to auto-execution only after validation.
- Keep market data provider and execution broker as separate concerns.

## Provider target

- Current runtime plumbing uses Binance `PAXG/USDT` proxy only for continuity.
- Current research/backtest validation source for real `XAUUSD` is Dukascopy public `.bi5`.
- Execution broker selection is separate from market-data provider selection.
- Keep provider and execution broker as separate concerns so provider can be swapped later.

## Target architecture

- Layer 1: data ingestion.
  Provider feed, four timeframes, PostgreSQL storage.
- Layer 2: strategy engine.
  Four strategies running in parallel and producing candidate signals.
- Layer 3: signal pipeline.
  Dedup, conflict filter, ranking, quota.
- Layer 4: backtesting and validation.
  Walk-forward optimization, Monte Carlo validation, regime detection, LHS optimizer.
- Layer 5: risk management.
  Daily loss limit, max positions, concentration check, ATR sizing, circuit breaker.
- Layer 6: execution.
  Signal mode first. Auto mode later.
- Layer 7: monitoring.
  Local web dashboard, health API, operational alerts, daily summary.

## Trading model target

- Trade only `XAUUSD`.
- Use four timeframes: `M15`, `H1`, `H4`, `D1`.
- Keep four strategies:
  `liquidity_sweep`
  `trend_continuation`
  `breakout_expansion`
  `ema_momentum`
- Keep signal pipeline stages:
  dedup
  conflict filter
  ranking
  quota

## Validation target

- Add walk-forward optimization with train/test windows.
- Add optimizer results storage and active parameter selection.
- Add Monte Carlo validation.
- Add regime detection and use it in ranking.
- Keep risk settings fixed in env.
  `.planning/PROJECT.md` marks risk as fixed, not optimized.

## Execution target

- Keep two execution modes:
  `signal`
  `auto`
- In signal mode:
  persist approved signals, expose them in the local dashboard, and track theoretical outcomes.
- In auto mode:
  place orders, attach SL/TP, partial at TP1, then trail.
- Gate the switch from signal to auto behind validation criteria.

## Monitoring target

- Keep `/health`.
- Keep `/dashboard` and `/api/dashboard` as the active operator surface.
- Persist local signal-mode decisions and lifecycle state before considering any external notification channel.
- Keep Redis in the design for rate limiting, cache, and circuit-breaker state.

## Planned build path

- Phases 1 to 4 are the implemented foundation path in the current repo:
  foundation
  data ingestion
  strategy engine
  signal pipeline
- Later planned phases from `.planning/ROADMAP.md` and `LAUNCH_PROMPT.md`:
  backtesting and validation
  risk management
  signal mode and monitoring
  auto mode

## Non-negotiables from existing project docs

- Stay on Python 3.12.
- Keep FastAPI, PostgreSQL, Redis, Docker Compose, SQLAlchemy async, Pydantic v2, APScheduler, structlog, `httpx`, `jinja2`, `scipy`, `numpy`, and `pandas` in the project stack.
- Keep `XAUUSD` as the only asset.
- Keep risk parameters out of the optimizer.
- Keep provider/broker separation so the market-data source can change without rewriting execution.
