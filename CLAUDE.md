# 0rum

Use this file for the current repository state.
Use `VISION.md` only for the intended end-state.
Treat `AGENTS.md`, `LAUNCH_PROMPT.md`, and older planning docs as broader product context, not proof that a module already exists.

## Read Order

When starting work:

1. Read this file.
2. Read `VISION.md` only if the task depends on the target architecture.
3. Read only the specific modules you need in `src/`.
4. Read `.planning/` only when a source comment explicitly points there or when historical phase context matters.

## Current Scope

Implemented in `src/`:

- ingestion
- four strategies
- signal pipeline persistence
- backtesting walk-forward core
- optimizer with Monte Carlo validation
- regime detection
- scheduler wiring
- risk module (`src/risk/`)
- execution module (`src/execution/`) for signal mode
- live `/health` wiring (postgres, redis, risk, signals, active strategies)
- local web dashboard (`GET /dashboard`, `GET /api/dashboard`) for operator monitoring
- Dukascopy research ingestion helpers for public `.bi5` planning, guarded batch fetches, cache QA, and Postgres import.

The repo currently covers the foundation through local signal-mode monitoring plumbing, not the full auto-execution target system described in `AGENTS.md`.

## Core Runtime

- `src/main.py`
  Configures `structlog`, launches startup ingestion as a background task using `backfill_all()`, starts APScheduler, then serves FastAPI.
- `src/ingestion/`
  `MarketDataClient` normalizes provider output to candle dicts.
  Default runtime plumbing provider is Binance via `XAUUSD -> PAXG/USDT`; this remains a proxy and is not validation-grade XAUUSD data.
- `src/ingestion/candle_fetcher.py`
  Parses normalized candle dicts into ORM rows and writes with PostgreSQL `INSERT ... ON CONFLICT DO NOTHING`.
  Runtime backfill is six months by bounded pagination windows.
- `src/ingestion/gap_detector.py`
  Scans for missing candles and explicitly ignores expected weekend gaps.
  Accepts `max_gap_bars` — gaps exceeding this limit are logged and skipped instead of triggering a fetch.
- `src/strategies/runner.py`
  Loads the latest 500 complete candles per timeframe from PostgreSQL, restores active optimizer params when present, then runs eligible strategies with `asyncio.gather(..., return_exceptions=True)`.
  Before the first optimizer result exists, it can bootstrap with `PARAM_RANGES` midpoints. After optimizer history exists, strategies without an active optimizer row are skipped as unvalidated.
- `src/pipeline/runner.py`
  Runs `dedup -> conflict -> regime -> rank -> quota -> persist`.
  Persistence is one transaction that writes the regime row, candidate rows with final statuses, then approved rows.
- `src/monitoring/health.py`
  Checks PostgreSQL and Redis, returns scheduler fetch timestamps, and includes live risk/status fields (`circuit_breaker`, `open_positions`, `daily_pnl_pct`, `signals_today`, `strategies_active`).
- `src/backtesting/historical_loader.py`
  Provides shared research candle record conversion and idempotent bulk insert helpers.
- `src/backtesting/optimizer.py`
  Evaluates deterministic LHS parameter samples across walk-forward windows, builds dense daily OOS PnL for Monte Carlo, and persists active params only when WFE, multi-window, and Monte Carlo gates pass.

## Current Provider Truth

- Runtime plumbing uses Binance `PAXG/USDT` only.
- Binance/PAXG is not validation-grade XAUUSD data.
- Research/backtest ingestion uses Dukascopy public `.bi5` for `XAUUSD`.
- Dukascopy uses price scale `/1000` and supports M15/H1/H4/D1 exports from tick cache.
- Dukascopy is not a runtime live provider.
- Market data provider and execution broker stay decoupled.
- Latest real optimizer rerun persisted one active strategy: `liquidity_sweep` with WFE `1.8478`, PF `2.5744`, 108 OOS trades.
- `trend_continuation` and `ema_momentum` failed Monte Carlo; `breakout_expansion` had no passing combo. Runtime skips these unvalidated strategies until an optimizer run activates them.

Provider matrix:

| Concern | Current status |
|---|---|
| Runtime market-data plumbing | Binance/CCXT `PAXG/USDT` proxy only |
| Research/backtest XAUUSD data | Dukascopy public `.bi5` |
| Execution broker | Not selected in this branch |
| Operator surface | Local web dashboard only |

## Invariants

- Import path is `src.*`.
- Keep DB access async with `AsyncSessionLocal`.
- Keep candle sequences oldest -> newest before indicator calculations.
- Preserve the normalized candle contract between ingestion layers:
  `time`, `open`, `high`, `low`, `close`, `volume`.
- Keep ORM and DTO layers separate:
  `src/models/signal.py` is ORM.
  `src/models/signal_data.py` holds Pydantic DTOs and enums.
- Every strategy must expose:
  `STRATEGY_NAME`
  `PARAM_RANGES`
  `async def generate_signals(...)`
- Each strategy is expected to keep exactly three optimizable params.
- Missing strategy params are filled from midpoints inside each strategy only for missing keys in an already-selected params dict.
- `StrategyRunner` midpoint fallback is bootstrap-only: it is allowed when `optimizer_results` has no rows yet, but once optimizer history exists, missing active params cause that strategy to be skipped.
- Keep `STRATEGY_NAME` values aligned with `optimizer_results.strategy`.
- Preserve `CandidateSignal` object identity through the pipeline unless you also rewrite status tracking.
  `PipelineRunner` uses `status_map[id(sig)]`.
- Treat PostgreSQL as the real runtime target.
  Some tests use SQLite only as scaffolding.

## Safe Modification Rules

- Keep strategies pure.
  Do not add DB writes or scheduler concerns inside strategies or `StrategyRunner`.
- Do not introduce RSI into `BreakoutExpansionStrategy`.
- Do not introduce MACD into `EmaMomentumStrategy`.
- Do not change candle dict keys without updating ingestion code and tests together.
- Source comments may reference old `CLAUDE.md` sections and `D-*` / `T-*` decision IDs.
  Those breadcrumbs point mainly to `.planning/phases/03-strategy-engine/03-CONTEXT.md` and `.planning/phases/04-signal-pipeline/04-CONTEXT.md`.

## Tests

- Env vars must exist before importing `src.*`.
  `tests/conftest.py` handles this for pytest.
- Strategy tests are partly source-structure tests.
  Refactors can fail tests even when behavior is unchanged.
- `tests/test_strategies/test_runner.py` checks for `asyncio.gather`, `return_exceptions=True`, and absence of DB writes.
- Pipeline tests mostly mock DB sessions and stage helpers.
- `tests/test_ingestion/test_gap_detector.py` uses raw SQLite DDL because the production schema is PostgreSQL-specific.
- No committed endpoint tests were found for startup or `/health`.

## Known Gaps And Pitfalls

- `get_settings()` is cached with `@lru_cache(maxsize=1)`; test code that needs env-specific behavior should instantiate `Settings()` directly.
- `src/main.py` does not wait for startup ingestion to finish before starting the scheduler (intentional — backfill runs in background).
- Redis is only used in `health.py` right now.
- Pipeline persistence creates `TradeORM` rows for approved signals.
- `ApprovedSignalORM.execution_status` is initially persisted as `PENDING` and updated to `SENT` after successful signal-mode delivery.
- `ranker.py` falls back to WFE `0.5` when no active optimizer row exists.
- `quota.py` counts approved signals by current UTC date.
- Do not assume a missing module exists just because `AGENTS.md` or a docstring mentions it.

## Useful Commands

- `uvicorn src.main:app --host 0.0.0.0 --port 8000`
- `pytest`
- `./.venv/bin/python scripts/dukascopy_fetch.py batch-plan --symbol XAUUSD --start 2020-01-01T00:00:00Z --end 2020-02-01T00:00:00Z --batch-days 5 --max-days 31`
- `./.venv/bin/python scripts/dukascopy_fetch.py batch-fetch --symbol XAUUSD --start 2020-01-01T00:00:00Z --end 2020-02-01T00:00:00Z --batch-days 5 --max-days 31 --timeframes M15 H1 H4 D1 --progress`
- `./.venv/bin/python scripts/dukascopy_fetch.py qa --symbol XAUUSD --start 2020-01-01T00:00:00Z --end 2020-02-01T00:00:00Z --timeframe M15`
- `./.venv/bin/python scripts/dukascopy_fetch.py import-postgres --symbol XAUUSD --start 2020-01-01T00:00:00Z --end 2020-02-01T00:00:00Z --timeframes M15 H1 H4 D1 --dry-run`

## Maintenance Rule

If implemented scope, invariants, or known pitfalls change materially, update this file so future sessions do not waste tokens rediscovering them.
