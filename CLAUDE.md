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
- offline HistData bootstrap loader
- regime detection
- scheduler wiring
- partial `/health`

Not implemented yet in `src/`:

- `src/risk/`
- `src/execution/`
- `src/monitoring/telegram_bot.py`

The repo currently covers the foundation through Phase 5 backtesting/validation plumbing, not the full target system described in `AGENTS.md`.

## Core Runtime

- `src/main.py`
  Configures `structlog`, launches startup ingestion as a background task (IG → `warm_up_all()`, Binance → `backfill_all()`), starts APScheduler, then serves FastAPI.
- `src/ingestion/`
  `MarketDataClient` normalizes provider output to candle dicts.
  Default runtime provider is Binance via `XAUUSD -> PAXG/USDT`.
  `IGClient` exists as the additive IG path.
- `src/ingestion/candle_fetcher.py`
  Parses normalized candle dicts into ORM rows and writes with PostgreSQL `INSERT ... ON CONFLICT DO NOTHING`.
  Backfill (Binance path) is six months by bounded pagination windows.
  On the IG path, `warm_up_all()` replaces backfill: one bounded `fetch_and_store` per timeframe using `count`, no date-range walk.
  Per-timeframe warm-up bar counts come from `Settings.ig_warmup_bars_*`.
- `src/ingestion/gap_detector.py`
  Scans for missing candles and explicitly ignores expected weekend gaps.
  Accepts `max_gap_bars` — gaps exceeding this limit are logged and skipped instead of triggering a fetch.
  Always passes `to_time=gap_end` to `fetch_and_store` so no open-ended historical request is issued on IG.
- `src/strategies/runner.py`
  Loads the latest 500 complete candles per timeframe from PostgreSQL, restores active optimizer params when present, then runs eligible strategies with `asyncio.gather(..., return_exceptions=True)`.
  Before the first optimizer result exists, it can bootstrap with `PARAM_RANGES` midpoints. After optimizer history exists, strategies without an active optimizer row are skipped as unvalidated.
- `src/pipeline/runner.py`
  Runs `dedup -> conflict -> regime -> rank -> quota -> persist`.
  Persistence is one transaction that writes the regime row, candidate rows with final statuses, then approved rows.
- `src/monitoring/health.py`
  Checks PostgreSQL and Redis, returns scheduler fetch timestamps, and still uses placeholders for several trading fields.
- `src/backtesting/historical_loader.py`
  Loads HistData Generic ASCII XAUUSD M1 ZIP archives, resamples to optimizer timeframes, and bulk inserts idempotently.
- `src/backtesting/optimizer.py`
  Evaluates deterministic LHS parameter samples across walk-forward windows, builds dense daily OOS PnL for Monte Carlo, and persists active params only when WFE, multi-window, and Monte Carlo gates pass.

## Current Trading Truth

- Project target is real `XAUUSD` through IG demo -> live.
- Current plumbing still supports Binance `PAXG/USDT` as a temporary proxy.
- Do not treat the Binance proxy as sufficient validation for later live-like phases.
- Phase 5 validation is backed by local HistData XAUUSD M1 archives resampled into M15/H1/H4/D1.
- Latest real optimizer rerun persisted one active strategy: `liquidity_sweep` with WFE `1.8478`, PF `2.5744`, 108 OOS trades.
- `trend_continuation` and `ema_momentum` failed Monte Carlo; `breakout_expansion` had no passing combo. Runtime skips these unvalidated strategies until an optimizer run activates them.

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
- If switching runtime ingestion to IG, set `MARKET_DATA_PROVIDER=ig` and provide `IG_XAUUSD_EPIC`.
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
- `tests/test_ingestion/test_ig_client.py` uses `respx`.
- No committed endpoint tests were found for startup or `/health`.

## Known Gaps And Pitfalls

- `get_settings()` is not cached even though its docstring says it is.
- `src/main.py` does not wait for startup ingestion to finish before starting the scheduler (intentional — backfill/warm-up runs in background).
- On the IG path, `warm_up_all()` issues one `fetch_and_store(count=N)` per timeframe. If the DB is already populated, those bars are silently skipped by `ON CONFLICT DO NOTHING` — no wasted API quota.
- `GapDetector.max_gap_bars` is only enforced when explicitly passed. The scheduler wires it for IG via `Settings.ig_max_gap_bars`; direct calls without the argument are unbounded.
- `/health` is only partially wired:
  `circuit_breaker`, `open_positions`, `daily_pnl_pct`, and `signals_today` are placeholders.
  `strategies_active` is hardcoded to `4`.
- Redis is only used in `health.py` right now.
- `TradeORM` exists, but current scheduler and pipeline code do not create trades.
- `ApprovedSignalORM.execution_status` is always persisted as `PENDING` by the current pipeline.
- `ranker.py` falls back to WFE `0.5` when no active optimizer row exists.
- `quota.py` counts approved signals by current UTC date.
- Do not assume a missing module exists just because `AGENTS.md` or a docstring mentions it.

## Useful Commands

- `uvicorn src.main:app --host 0.0.0.0 --port 8000`
- `pytest`
- `python scripts/ig_demo_probe.py`
- `python scripts/ig_demo_probe.py --search gold`
- `python scripts/ig_demo_probe.py --count 3`
- `python scripts/histdata_phase5_loader.py qa`
- `python scripts/histdata_phase5_loader.py import`

## Maintenance Rule

If implemented scope, invariants, or known pitfalls change materially, update this file so future sessions do not waste tokens rediscovering them.
