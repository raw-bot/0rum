# Phase 6: Risk Management - Research

**Researched:** 2026-04-26
**Domain:** Pre-trade risk gates + ATR position sizing + Redis-backed circuit breaker (Python 3.12 / SQLAlchemy 2.0 async / Pydantic v2 / structlog / fakeredis)
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Theoretical-position data source**
- **D-01:** `TradeORM` is the single read-only source of truth for "open positions", "daily P&L", and "consecutive stops". Phase 6 NEVER inserts or updates `TradeORM` rows — Phase 7 (SIG-02) will. When `TradeORM` is empty (the normal state until Phase 7 ships), every count returns 0 and gates pass; this is intentional and tests must cover both populated and empty states.
- **D-14:** Daily P&L formula:
  ```sql
  SELECT COALESCE(SUM(pnl_pct), 0)
    FROM trades
   WHERE closed_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC')
     AND status = 'CLOSED'
  ```
  Open trades do NOT contribute (no mark-to-market). RISK-01 trip condition: `daily_pnl_pct <= -0.03` (i.e., `daily_pnl_pct <= settings.daily_loss_limit`).
- **D-09:** Equity baseline = new env var `theoretical_equity_usd` (default `Decimal("10000")`) added to `src/config.py`. Used by the sizer to convert `risk_pct` → `size_lots`. Dynamic equity (apply daily realized P&L) is deferred — constant baseline keeps Phase 6 self-contained.

**Module location & pipeline integration**
- **D-02:** New package `src/risk/` houses ALL risk logic. `src/pipeline/runner.py` calls a single public interface (e.g., `RiskGateRunner.evaluate(candidate, regime, h1_candles, session) -> RiskDecision`) and never imports gate-internal helpers. Pipeline does NOT absorb gate logic.
- **D-03:** Risk gates run as a NEW pipeline step inserted between the existing `quota` step (step 5) and `_persist` (step 6). The new step is conceptually `step 5.5: risk gates`. Quota stays exactly as-is — `MAX_SIGNALS_PER_DAY` (5 signals/calendar UTC day) is semantically distinct from RISK-02's `max_positions` (5 concurrent open theoretical positions).
- **D-05:** Rejection auditing = structlog only in Phase 6. The status of a risk-rejected `CandidateSignalORM` stays `REJECTED` (existing enum); the reason is captured in a structured log event `risk.gate.rejected` with fields `gate`, `reason`, `strategy`, `direction`, `entry_price`, and optionally `signal_ref=id(sig)`. No DB `candidate_id` exists pre-persist; do NOT add a `rejection_reason` column.

**RISK-03 concentration semantics (overrides REQUIREMENTS.md text)**
- **D-04:** RISK-03 REDUCES the new trade size by 50% when 4+ positions are open in the same direction. It does NOT block. Aligns with `AGENTS.md` §12.1 Gate 3. The signal still passes the gate (`risk_check_passed=True`); the sizer downstream halves the lot size. Reflected in the gate decision payload, not via REJECTED status.

**ATR sizing (RISK-04)**
- **D-06:** ATR window/timeframe = ATR(14) on H1. Sizer DOES NOT recompute — it consumes `MarketRegime.atr_value` and `MarketRegime.atr_pctile` already produced by `RegimeDetector.detect()`.
- **D-07:** Symmetric volatility scaling per AGENTS.md §12.2:
  - `atr_pctile >= atr_high_vol_percentile / 100` → `risk_pct *= 0.7`
  - `atr_pctile <= atr_low_vol_percentile / 100` → `risk_pct *= 1.3`
  - else → `× 1.0`

  **Scale invariant:** `MarketRegime.atr_pctile` is on a 0.0–1.0 scale (`0.90` = 90th pctile). Settings store whole numbers (90 / 10). Sizer MUST normalize settings to `0.90` / `0.10` before comparing. Do NOT compare `atr_pctile` directly to `90` or `10`.

  **Order of operations:** `risk_pct = risk_per_trade × vol_factor`, THEN `risk_pct = min(risk_pct, hard_cap_risk)` (2% cap applied AFTER vol adjustment so high-vol bump can never escape the cap), THEN `risk_pct *= 0.5` if concentration triggered (D-04). Hard cap = max for non-concentrated trade; concentration further halves whatever survived the cap.
- **D-16:** Symmetric "+30% in low-vol" branch IS in scope (per AGENTS.md §12.2), even though ROADMAP only mentions the -30% high-vol branch. Verifier should not treat the low-vol branch as scope creep.
- **D-08:** Position sizer is a pure function:
  ```python
  def calculate_position_size(
      *,
      equity: Decimal,
      risk_per_trade: float,
      entry_price: Decimal,
      sl_price: Decimal,
      atr_value: Decimal,
      atr_pctile: float,
      hard_cap: float,
      atr_high_vol_pctile: int,
      atr_low_vol_pctile: int,
      same_direction_open_count: int,
  ) -> PositionSizing
  ```
  Returns `PositionSizing` Pydantic DTO with `risk_pct`, `risk_amount_usd`, `size_lots`, `vol_factor`, `concentration_reduced` (bool). Phase 6 does NOT add `size_lots` to `ApprovedSignalORM` — Phase 7 will decide where to persist.

**Circuit breaker (RISK-05)**
- **D-10:** Redis state under `risk:cb:` prefix:
  - `risk:cb:consecutive_stops` (int counter, no TTL)
  - `risk:cb:tripped_at` (ISO-8601 string; absent when not tripped)
  - `risk:cb:cooldown_until` (ISO-8601 string; written with Redis TTL = `circuit_breaker_cooldown_hours × 3600` so keys expire automatically)
- **D-11:** `BreakerManager` API:
  - `is_tripped() -> bool` — true iff `tripped_at` set AND `cooldown_until` in the future; reads only.
  - `record_stop(trade_id, strategy) -> CircuitBreakerAlert | None` — Phase 7 calls when a `TradeORM` row closes with `close_reason='SL'`; returns the alert event ONLY when this stop is the trip.
  - `record_win() -> None` — Phase 7 calls on any closed-with-profit trade; resets counter to 0.
  - `reset_if_expired() -> bool` — called at the start of every gate evaluation; deletes `tripped_at`/`cooldown_until` and resets `consecutive_stops` to 0 if cooldown elapsed; idempotent.

  Reset rule (AGENTS.md §12.3): counter resets on first winning trade OR end of cooldown.
- **D-12:** While breaker `is_tripped()`, gate evaluator short-circuits ALL candidates to `REJECTED` with reason `circuit_breaker_active`. No daily-loss / max-positions / concentration evaluation runs. Sizer is not invoked.
- **D-15:** RISK-01 (daily loss limit) does NOT trip the circuit breaker in Phase 6. AGENTS.md §12.1 Gate 1 hint is deferred. Strict ROADMAP behavior: block + structlog event, no breaker trip.

**Telegram alert delivery (deferred to Phase 7)**
- **D-13:** Phase 6 defines `CircuitBreakerAlert` (Pydantic v2 DTO in `src/risk/events.py`):
  - `tripped_at: datetime`
  - `consecutive_stops: int`
  - `cooldown_until: datetime`
  - `last_stop_strategy: str`
  - `last_stop_trade_id: UUID | None`

  When `BreakerManager.record_stop()` returns a non-None alert, Phase 6 emits structlog `risk.circuit_breaker.tripped` AND publishes the alert through a simple in-process hook interface (`BreakerAlertHook` callable list) that Phase 7's NOTIF-03 will register against. **No `python-telegram-bot` import in Phase 6.** `src/monitoring/telegram_bot.py` stays unimplemented.

**Mode coverage**
- **D-13b:** Phase 6 targets signal mode only. "Consecutive stops" = theoretical stops (`TradeORM.close_reason = 'SL'` AND `status = 'CLOSED'`). Use existing schema convention (`SL`, `TP1`, `TP2`, `TRAIL`, `MANUAL`, `CIRCUIT_BREAKER`) — do NOT introduce `sl_hit`. Auto-mode broker-fill stops will be wired in Phase 8/9; the breaker contract is mode-agnostic.

### Claude's Discretion

- Internal structure of `src/risk/` (single module vs split). Recommended: split into `gates.py`, `sizer.py`, `breaker.py`, `events.py`, `runner.py`, mirroring `src/pipeline/`.
- Whether `RiskGateRunner.evaluate` lives in `src/risk/runner.py` or `src/pipeline/risk.py`. Recommended: `src/risk/runner.py` so `pipeline/runner.py` imports only from `src.risk`.
- Whether unit tests use `fakeredis` or live Redis. Recommended: `fakeredis.FakeAsyncRedis` for unit tests; one integration test against real Redis from `docker-compose.yml`.
- Exact `RiskDecision` schema (e.g., `passed: bool`, `reason: str | None`, `sizing: PositionSizing | None`, `concentration_reduced: bool`).
- Logging key naming convention (`risk.gate.daily_loss_limit.rejected` vs flat `risk.gate.rejected` with `gate=` field). Either works.
- Whether `theoretical_equity_usd` is `Decimal` or `float` in Settings (project leans Decimal in models — recommended).
- Test fixture strategy for empty-TradeORM case (mock `AsyncSession.execute` returning empty/zero vs populated test DB). Recommended: mock at session level for unit tests; one integration test against real DB.

### Deferred Ideas (OUT OF SCOPE)

- Telegram client (`src/monitoring/telegram_bot.py`) — Phase 7 (NOTIF-01..04). Phase 6 produces alert event; Phase 7 delivers.
- Theoretical-trade lifecycle (open → TP1 → trailing → close) populating `TradeORM` — Phase 7 (SIG-01..03).
- Auto-mode risk handling (real broker fills, real positions) — Phase 8/9.
- Daily-loss-limit tripping the circuit breaker — possible v2; not in ROADMAP success criterion (D-15).
- Persisting `rejection_reason` on `candidate_signals` and `size_lots` / `risk_pct` on `approved_signals` — Phase 7 if structured DB audit needed; logs cover v1 (D-05, D-08).
- Dynamic equity (apply realized daily P&L to baseline) — currently constant `theoretical_equity_usd` (D-09).
- RISK-03 alternate semantics (block instead of reduce) — explicitly rejected per D-04.
- Auto-mode "consecutive stops" against real broker fills — same breaker contract should hold; revisit only if broker fill semantics differ.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| RISK-01 | Daily loss limit gate blocks all new signals if cumulative theoretical loss ≥ −3% | SQLAlchemy 2.0 async query against `TradeORM` with `func.coalesce(func.sum(...), 0)` filtered by UTC-day boundary (see Code Examples §1) |
| RISK-02 | Max positions gate blocks new signals if concurrent theoretical positions ≥ 5 | `select(func.count()).where(TradeORM.status == 'OPEN')` (Code Examples §2) |
| RISK-03 | Concentration check — REDUCE size 50% when 4+ same-direction open (per D-04, NOT block) | `select(TradeORM.direction, func.count()).group_by(...)` (Code Examples §3); halving applied in sizer after hard cap |
| RISK-04 | ATR-based position sizing with vol adjustment and 2% hard cap | Pure function consuming `MarketRegime.atr_pctile` (0–1 scale); order: vol → cap → concentration (D-07) |
| RISK-05 | Circuit breaker: 24h shutdown after 8 consecutive SL, sends Telegram alert | `redis.asyncio` (already imported in `src/monitoring/health.py`); `fakeredis.FakeAsyncRedis` for tests; alert via in-process hook (D-13) |

**Note on RISK-03 wording mismatch:** REQUIREMENTS.md says "blocks signals that would over-expose the same direction." Locked decision **D-04** overrides this to **REDUCE size by 50%, do NOT block**, per AGENTS.md §12.1 Gate 3. The verifier and plan-checker MUST treat D-04 as authoritative.
</phase_requirements>

## Summary

Phase 6 is mostly assembly work, not invention. Every threshold (`risk_per_trade`, `daily_loss_limit`, `max_positions`, `circuit_breaker_stops`, `circuit_breaker_cooldown_hours`, `hard_cap_risk`, `atr_high_vol_percentile`, `atr_low_vol_percentile`) is already in `src/config.py:56-65`. Every read-side ORM field is already on `TradeORM` (`status`, `pnl_pct`, `close_reason`, `closed_at`, `direction`). `MarketRegime.atr_value` and `atr_pctile` are already produced by `RegimeDetector.detect()` in step 3 of `PipelineRunner.run()`, immediately upstream of where the new risk step will land. Redis is already plumbed via `redis.asyncio` (the project ships `redis>=5.0.0`; current installed version is `7.1.0`) and is exercised once today in `src/monitoring/health.py`.

The novel work is (a) the `BreakerManager` Redis state machine, (b) the pure ATR sizer matching AGENTS.md §12.2 line-for-line with the order-of-operations from D-07, (c) the `RiskGateRunner` that owns the breaker short-circuit (D-12) → daily-loss → max-positions → concentration → sizer flow, and (d) wiring it into `PipelineRunner.run()` between line 90 (`apply_quota`) and line 105 (`_persist`).

The biggest correctness traps are the `atr_pctile` scale (0–1 in code vs 0–100 in settings — D-07), the order-of-operations on the sizer (vol-then-cap-then-concentration, NOT cap-then-vol — D-07), the empty-`TradeORM` invariant (gates must pass cleanly when there are no closed trades — D-01), and ensuring risk DB reads do not share the `_persist` transaction in `PipelineRunner` (the risk step runs BEFORE `_persist` opens its single transaction; risk gates open their own short-lived sessions or accept an explicitly read-only session).

**Primary recommendation:** Implement `src/risk/{events,sizer,breaker,gates,runner}.py` mirroring `src/pipeline/` layout. Use `fakeredis.FakeAsyncRedis` for unit tests. Mock `AsyncSession.execute()` to return `MagicMock(scalar_one=...)` for gate unit tests; reserve one integration test per stateful component (breaker against real Redis, gates against real Postgres).

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Daily P&L aggregation | Database (PostgreSQL) | Backend (`src/risk/gates.py`) | `SUM(pnl_pct)` with UTC-day filter is canonically a SQL aggregate; Python wraps the query and applies the threshold |
| Open-position counting | Database (PostgreSQL) | Backend (`src/risk/gates.py`) | `COUNT(*)` with `status='OPEN'` filter is a SQL aggregate |
| Direction concentration | Database (PostgreSQL) | Backend (`src/risk/gates.py`) | `GROUP BY direction` SQL aggregate; backend interprets count vs threshold |
| ATR sizing math | Backend (`src/risk/sizer.py`) — pure function | — | No I/O; consumes already-computed `MarketRegime` from pipeline scope |
| Circuit breaker state | Cache (Redis) | Backend (`src/risk/breaker.py`) | Cross-process counter with TTL → Redis is the only viable store; `redis.asyncio` is the client |
| Alert dispatch | Backend (`src/risk/runner.py` hook surface) | Backend (Phase 7 Telegram in `src/monitoring/telegram_bot.py`) | Phase 6 emits structured event; Phase 7 owns the side effect |
| Pipeline integration | Backend (`src/pipeline/runner.py`) | Backend (`src/risk/runner.py`) | Pipeline orchestrates; risk runner is a black box behind `RiskGateRunner.evaluate()` |
| Health visibility | Backend (`src/monitoring/health.py`) | Cache + Database | `circuit_breaker` from Redis read; `open_positions`/`daily_pnl_pct` from gate helpers |

## Project Constraints (from CLAUDE.md)

- Import path is `src.*`. New package is `src/risk/`.
- Keep DB access async with `AsyncSessionLocal` (`src/database.py:18`).
- Keep candle sequences oldest → newest before indicator calculations.
- ORM and DTO layers stay separate: `src/risk/events.py` for Pydantic DTOs, no ORM mutations from Phase 6 (Phase 6 is read-only against `TradeORM`).
- Async everywhere on I/O paths (`async def` on every gate evaluator and breaker method).
- structlog at module level: `log = structlog.get_logger(__name__)`. No `print()`.
- Pydantic v2 (`Field`, `model_config`); SQLAlchemy 2.0 (`Mapped`, `mapped_column`, `select(...)`).
- `pytest-asyncio` with `asyncio_mode = "auto"` (already in `pyproject.toml:38`); env vars set in `tests/conftest.py` BEFORE any `src.*` import.
- Tests under `tests/test_risk/` mirroring `tests/test_pipeline/` layout.
- Treat PostgreSQL as the real runtime target. Some tests use SQLite as scaffolding only — risk gates use Postgres-specific `func.date_trunc` and should be tested via either a Postgres test container OR by mocking `session.execute()`.
- "Redis is only used in `health.py` right now" — Phase 6 will be the second runtime consumer.
- "Do not assume a missing module exists just because `AGENTS.md` mentions it." (`src/risk/`, `src/execution/`, `src/monitoring/telegram_bot.py` are all NOT yet implemented.)
- Forbidden patterns: no LSTM/sklearn, no MACD/RSI, no multi-asset (none apply to risk module — none of these would naturally appear in risk code; just don't accidentally reintroduce them via copy-paste from elsewhere).

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `redis` (redis-py) | `>=5.0.0` (installed 7.1.0) [VERIFIED: `pip show redis`] | Async Redis client for `BreakerManager` | Already a project dep; `redis.asyncio` is the canonical async API since redis-py 4.2 (`aioredis` was deprecated and merged in). Already used in `src/monitoring/health.py:5` as `import redis.asyncio as aioredis` |
| `sqlalchemy[asyncio]` | `>=2.0.0` [VERIFIED: pyproject.toml:11] | Async ORM for `TradeORM` reads | Project standard; already used in `src/pipeline/quota.py` for `func.count()` aggregates |
| `pydantic` | `>=2.7.0` [VERIFIED: pyproject.toml:15] | DTOs (`RiskDecision`, `PositionSizing`, `CircuitBreakerAlert`) | Project standard; mirrors `src/models/signal_data.py:CandidateSignal` pattern |
| `pydantic-settings` | `>=2.3.0` [VERIFIED: pyproject.toml:16] | Adding `theoretical_equity_usd` to `Settings` | Project standard |
| `structlog` | `>=24.2.0` [VERIFIED: pyproject.toml:21] | Structured event logging for rejections + alerts | Project standard; `log = structlog.get_logger(__name__)` at module top |

### Supporting (test-only)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `fakeredis` | `>=2.x` [VERIFIED: PyPI release notes — `cunla/fakeredis-py` is the active fork] [CITED: https://fakeredis.readthedocs.io/] | In-memory Redis for `BreakerManager` unit tests | All breaker unit tests |
| `pytest-asyncio` | `>=0.23.0` [VERIFIED: pyproject.toml:27] | `async def test_*` support; `asyncio_mode = "auto"` is already configured | All async tests |

**Installation (test-only addition):**
```bash
# Add to pyproject.toml [project.dependencies] OR move to optional [project.optional-dependencies.test]
pip install 'fakeredis>=2.20'
```

> Recommended: add `fakeredis>=2.20` as a hard dep in `pyproject.toml` since the project keeps test deps in the main `dependencies` list (see `pytest`, `pytest-asyncio`, `aiosqlite`, `respx` already there). This matches existing project convention. [CITED: pyproject.toml:9-30]

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `redis>=5.0.0` (`redis.asyncio`) | `aioredis` standalone | `aioredis` is deprecated and merged into redis-py since 4.2. Using anything other than `redis.asyncio` would break the existing pattern in `src/monitoring/health.py` and add a redundant dep. [CITED: https://github.com/redis/redis-py — aioredis-py is archived in favor of redis-py asyncio API] |
| `fakeredis.FakeAsyncRedis` | `pytest-mock` + manual async stubs | Manual stubs would re-implement Redis semantics (TTL math, atomic INCR) and risk false-pass tests. fakeredis runs the real Redis state machine in-process. |
| Live Redis container in unit tests | docker-compose service | Slow (Docker spin-up per test session), fragile (requires Docker on the runner), and contradicts the project's documented preference for Docker-free unit tests (CONTEXT.md "Specifics" §5). Reserve for ONE integration test. |

**Version verification:**
```bash
# redis-py is already installed
pip show redis  # Name: redis  Version: 7.1.0  (verified 2026-04-26)
```

## Architecture Patterns

### System Architecture Diagram

```
                            ┌────────────────────────────────────┐
                            │  scheduler/jobs.py: run_pipeline   │
                            │  (every 15min, max_instances=1)    │
                            └─────────────────┬──────────────────┘
                                              │
                                              ▼
                            ┌────────────────────────────────────┐
                            │ StrategyRunner.run() →             │
                            │ list[CandidateSignal]              │
                            └─────────────────┬──────────────────┘
                                              │
                                              ▼
                            ┌────────────────────────────────────┐
                            │      PipelineRunner.run()          │
                            │  candidates + h1_candles           │
                            └─────────────────┬──────────────────┘
                                              │
       ┌──────────────────────────────────────┼──────────────────────────────────────┐
       │                                      │                                      │
       ▼ Step 1: dedup    Step 2: conflict    Step 3: regime    Step 4: rank    Step 5: quota
       │                                      │                                      │
       └──────────────────────────────────────┴──────────────────────────────────────┘
                                              │
                                              ▼
                  ┌───────────────────────────────────────────────────────┐
                  │  *** NEW Step 5.5: RiskGateRunner.evaluate() ***      │
                  │  src/risk/runner.py                                   │
                  └─────────────────┬─────────────────────────────────────┘
                                    │
            ┌───────────────────────┼─────────────────────────┐
            │                       │                         │
            ▼ short-circuit?        ▼ for each survivor       │
   ┌──────────────────┐    ┌──────────────────────────┐       │
   │ BreakerManager   │    │  Gate 1: daily_loss      │ ──▶   │ TradeORM (read-only)
   │ .reset_if_       │    │   COALESCE(SUM(pnl_pct)) │       │ status=CLOSED,
   │  expired()       │    │   WHERE closed_at >= UTC │       │ closed_at >= UTC day
   │ .is_tripped() ──▶│    │   day, status=CLOSED     │       │
   └──────┬───────────┘    └──────────┬───────────────┘       │
          │                           │                       │
          │ tripped                   ▼                       │
          ▼                  ┌──────────────────────────┐     │
   reject ALL with reason   │  Gate 2: max_positions   │ ──▶ │ TradeORM (read-only)
   "circuit_breaker_active" │   COUNT(*) status=OPEN   │     │ status=OPEN
                             └──────────┬───────────────┘     │
                                        │                     │
                                        ▼                     │
                             ┌──────────────────────────┐     │
                             │  Gate 3: concentration   │ ──▶ │ TradeORM (read-only)
                             │   COUNT(*) WHERE status= │     │ status=OPEN GROUP BY direction
                             │   OPEN GROUP BY direction│     │
                             └──────────┬───────────────┘     │
                                        │                     │
                                        ▼                     │
                             ┌──────────────────────────┐     │
                             │  Sizer (pure):           │     │
                             │   risk_pct × vol_factor  │     │
                             │   → min(_, hard_cap)     │     │
                             │   → × 0.5 if concentrate │     │
                             │   → size_lots            │     │
                             └──────────┬───────────────┘     │
                                        │                     │
                                        ▼                     │
                            RiskDecision { passed, reason,    │
                                            sizing,           │
                                            concentration_    │
                                              reduced }       │
                                              │               │
                                              ▼               │
                            PipelineRunner builds status_map  │
                                              │               │
                                              ▼               │
                                    Step 6: _persist          │
                                    (single transaction)      │
                                              │               │
                                              ▼               │
                                    PostgreSQL ◀──────────────┘

                                    ┌─────────────────────────┐
                            Phase 7 │ TradeORM closes with    │  ┌────────────────────┐
                          ─────────▶│ close_reason='SL':      │─▶│ BreakerManager     │
                                    │  call .record_stop()    │  │ .record_stop()     │
                                    │ close_reason in {TP1,   │  │  → INCR counter    │
                                    │  TP2, TRAIL,MANUAL}+win:│  │  → if ≥ N: trip    │
                                    │  call .record_win()     │  │  → emit            │
                                    └─────────────────────────┘  │   CircuitBreaker   │
                                                                 │   Alert            │
                                                                 └─────────┬──────────┘
                                                                           │
                                                                           ▼
                                                                ┌────────────────────┐
                                                                │ Redis (risk:cb:*)  │
                                                                │ + structlog event  │
                                                                │ + in-process hook  │
                                                                │   list (Phase 7    │
                                                                │   registers)       │
                                                                └────────────────────┘
```

**Component responsibilities:**

| Component | File | Responsibility |
|-----------|------|----------------|
| `RiskGateRunner` | `src/risk/runner.py` | Orchestrates breaker short-circuit → 3 gates → sizer; returns `RiskDecision` |
| 3 gate functions | `src/risk/gates.py` | Pure async functions taking a `session` + `settings`, returning `(passed: bool, reason: str | None, ctx: dict)` |
| `calculate_position_size` | `src/risk/sizer.py` | Pure sync function returning `PositionSizing` DTO; no I/O |
| `BreakerManager` | `src/risk/breaker.py` | Redis-backed state machine; methods `is_tripped`, `record_stop`, `record_win`, `reset_if_expired` |
| DTOs | `src/risk/events.py` | `RiskDecision`, `PositionSizing`, `CircuitBreakerAlert`, `BreakerAlertHook` (Pydantic v2) |
| Settings addition | `src/config.py` | `theoretical_equity_usd: Decimal = Decimal("10000")` |
| Pipeline wiring | `src/pipeline/runner.py` | Insert `RiskGateRunner.evaluate()` call between `apply_quota` and `_persist`; mark failed survivors REJECTED in `status_map` |
| Health wiring | `src/monitoring/health.py` | Replace 3 placeholders (`circuit_breaker`, `open_positions`, `daily_pnl_pct`) with real values |

### Recommended Project Structure
```
src/
├── risk/                              # NEW PACKAGE
│   ├── __init__.py                    # re-export RiskGateRunner, BreakerManager
│   ├── events.py                      # Pydantic DTOs (RiskDecision, PositionSizing, CircuitBreakerAlert)
│   ├── sizer.py                       # pure: calculate_position_size(...)
│   ├── gates.py                       # 3 async gate functions (DB reads via passed-in session)
│   ├── breaker.py                     # BreakerManager (Redis-backed)
│   └── runner.py                      # RiskGateRunner.evaluate(...)
└── pipeline/
    └── runner.py                      # MODIFIED: insert risk step between quota and _persist

tests/
└── test_risk/                         # NEW DIRECTORY (mirror tests/test_pipeline/)
    ├── __init__.py
    ├── test_sizer.py                  # pure-function tests, no fixtures
    ├── test_gates.py                  # mock AsyncSession.execute()
    ├── test_breaker.py                # fakeredis.FakeAsyncRedis
    ├── test_runner.py                 # full RiskGateRunner; mock breaker + gates + sizer
    └── test_pipeline_integration.py   # PipelineRunner with risk step (mock all DB + redis)
```

### Pattern 1: Pure-function sizer
**What:** Synchronous Python function with all inputs as kwargs; returns immutable Pydantic DTO. No I/O.
**When to use:** RISK-04 sizing math. Mirrors the existing pure-function pattern of `dedup_signals`, `filter_conflicts`, `apply_quota` (which is async only because it queries the DB; the math itself is pure).
**Example:**
```python
# src/risk/sizer.py — derived from AGENTS.md §12.2 (line-for-line per CONTEXT.md "Specifics")
from decimal import Decimal
from src.risk.events import PositionSizing

def calculate_position_size(
    *,
    equity: Decimal,
    risk_per_trade: float,
    entry_price: Decimal,
    sl_price: Decimal,
    atr_value: Decimal,        # accepted for future use; not used in v1 math
    atr_pctile: float,          # 0.0–1.0 scale (D-07 invariant)
    hard_cap: float,
    atr_high_vol_pctile: int,   # whole number, e.g. 90
    atr_low_vol_pctile: int,    # whole number, e.g. 10
    same_direction_open_count: int,
) -> PositionSizing:
    """ATR-based position sizer per AGENTS.md §12.2.

    Order of operations (D-07):
      1. risk_pct = risk_per_trade × vol_factor
      2. risk_pct = min(risk_pct, hard_cap)
      3. risk_pct *= 0.5 if same_direction_open_count >= 4
    """
    # Normalize whole-number percentile thresholds (settings) to 0–1 scale (atr_pctile)
    high_threshold = atr_high_vol_pctile / 100.0
    low_threshold = atr_low_vol_pctile / 100.0

    if atr_pctile >= high_threshold:
        vol_factor = 0.7
    elif atr_pctile <= low_threshold:
        vol_factor = 1.3
    else:
        vol_factor = 1.0

    risk_pct = risk_per_trade * vol_factor
    # Apply hard cap AFTER vol adjustment so high-vol bump can never escape the cap
    risk_pct = min(risk_pct, hard_cap)

    concentration_reduced = same_direction_open_count >= 4
    if concentration_reduced:
        risk_pct *= 0.5

    risk_amount = equity * Decimal(str(risk_pct))
    sl_distance = abs(entry_price - sl_price)
    if sl_distance == 0:
        size_lots = Decimal("0")
    else:
        # XAUUSD: 1 lot = 100 oz (per AGENTS.md §12.2)
        size_lots = (risk_amount / (sl_distance * Decimal("100"))).quantize(Decimal("0.01"))

    return PositionSizing(
        risk_pct=risk_pct,
        risk_amount_usd=risk_amount,
        size_lots=size_lots,
        vol_factor=vol_factor,
        concentration_reduced=concentration_reduced,
    )
```

### Pattern 2: Redis-backed counter with TTL
**What:** Atomic `INCR` for counter; SET with `ex=` for cooldown TTL; `EXISTS`/`GET` for state checks.
**When to use:** `BreakerManager` — cross-process state; counter survives restarts.
**Example:**
```python
# src/risk/breaker.py
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import redis.asyncio as aioredis
import structlog

from src.config import get_settings
from src.risk.events import CircuitBreakerAlert

log = structlog.get_logger(__name__)

CB_COUNTER = "risk:cb:consecutive_stops"
CB_TRIPPED_AT = "risk:cb:tripped_at"
CB_COOLDOWN_UNTIL = "risk:cb:cooldown_until"


class BreakerManager:
    def __init__(self, redis: aioredis.Redis | None = None):
        settings = get_settings()
        self._redis = redis or aioredis.from_url(settings.redis_url, decode_responses=True)
        self._stops_threshold = settings.circuit_breaker_stops
        self._cooldown_seconds = settings.circuit_breaker_cooldown_hours * 3600

    async def is_tripped(self) -> bool:
        cooldown = await self._redis.get(CB_COOLDOWN_UNTIL)
        if cooldown is None:
            return False
        # cooldown_until in the future → still tripped
        return datetime.fromisoformat(cooldown) > datetime.now(timezone.utc)

    async def reset_if_expired(self) -> bool:
        cooldown = await self._redis.get(CB_COOLDOWN_UNTIL)
        if cooldown is None:
            return False
        if datetime.fromisoformat(cooldown) <= datetime.now(timezone.utc):
            await self._redis.delete(CB_TRIPPED_AT, CB_COOLDOWN_UNTIL)
            await self._redis.set(CB_COUNTER, 0)
            log.info("risk.circuit_breaker.reset", reason="cooldown_expired")
            return True
        return False

    async def record_stop(
        self, trade_id: UUID | None, strategy: str
    ) -> Optional[CircuitBreakerAlert]:
        new_count = await self._redis.incr(CB_COUNTER)
        if new_count >= self._stops_threshold:
            tripped_at = datetime.now(timezone.utc)
            cooldown_until = tripped_at + timedelta(seconds=self._cooldown_seconds)
            await self._redis.set(CB_TRIPPED_AT, tripped_at.isoformat())
            await self._redis.set(
                CB_COOLDOWN_UNTIL, cooldown_until.isoformat(), ex=self._cooldown_seconds
            )
            alert = CircuitBreakerAlert(
                tripped_at=tripped_at,
                consecutive_stops=new_count,
                cooldown_until=cooldown_until,
                last_stop_strategy=strategy,
                last_stop_trade_id=trade_id,
            )
            log.warning(
                "risk.circuit_breaker.tripped",
                consecutive_stops=new_count,
                cooldown_until=cooldown_until.isoformat(),
            )
            return alert
        return None

    async def record_win(self) -> None:
        await self._redis.set(CB_COUNTER, 0)
        log.info("risk.circuit_breaker.counter_reset", reason="winning_trade")
```

### Pattern 3: Async DB read against `TradeORM` with UTC-day boundary
**What:** SQLAlchemy 2.0 async `select()` with `func.coalesce(func.sum(...), 0)` and `func.date_trunc('day', func.now() AT TIME ZONE 'UTC')`.
**When to use:** RISK-01 daily P&L gate.
**Example:** see Code Examples §1.

### Pattern 4: In-process hook list for cross-phase event delivery
**What:** Module-level list of callables; Phase 6 publishes via iteration; Phase 7 appends a callable.
**When to use:** `CircuitBreakerAlert` delivery without importing Phase 7's Telegram client (D-13).
**Example:**
```python
# src/risk/runner.py (excerpt)
from typing import Callable, Awaitable
from src.risk.events import CircuitBreakerAlert

BreakerAlertHook = Callable[[CircuitBreakerAlert], Awaitable[None]]
_alert_hooks: list[BreakerAlertHook] = []

def register_alert_hook(hook: BreakerAlertHook) -> None:
    """Phase 7 NOTIF-03 calls this at startup to register the Telegram sender."""
    _alert_hooks.append(hook)

async def _publish_alert(alert: CircuitBreakerAlert) -> None:
    for hook in _alert_hooks:
        try:
            await hook(alert)
        except Exception as exc:
            log.error("risk.alert_hook.failed", error=str(exc))
```

### Anti-Patterns to Avoid

- **Reusing the `_persist` transaction for risk reads.** `PipelineRunner._persist` opens a single transaction at line 144. The risk step runs BEFORE that, so it must open its own short-lived `AsyncSessionLocal()` context (or accept an explicit read-only session). Wrapping risk reads in `_persist`'s transaction would couple the two and prevent the risk step from running before persistence is decided.
- **Comparing `atr_pctile` (0.0–1.0) directly to settings (`90`, `10`).** This silently fails: `atr_pctile=0.95` is never `>= 90`. Always normalize: `high_threshold = settings.atr_high_vol_percentile / 100.0`. The existing `RegimeDetector._calculate_atr_percentile()` (lines 196–229) returns 0.0–1.0; the existing `RegimeDetector.detect()` line 50 already uses `atr_pctile >= 0.90`, confirming the scale. (D-07)
- **Recomputing ATR in the sizer.** `MarketRegime.atr_value` and `MarketRegime.atr_pctile` are produced by `RegimeDetector.detect()` in step 3 of `PipelineRunner.run()` (line 81); they are in scope at line 90 and beyond. Recomputing wastes cycles and risks divergence. (D-06)
- **Applying the hard cap BEFORE the vol adjustment.** Cap-then-vol means a low-vol +30% bump can push past the 2% ceiling. AGENTS.md §12.2 and D-07 specify vol-then-cap. The order is load-bearing.
- **Treating an empty `TradeORM` as an error.** Until Phase 7 ships, `TradeORM` is empty. Every gate must return "passed" cleanly when counts/sums are 0/None. `func.coalesce(func.sum(...), 0)` is mandatory; never use `result.scalar_one()` without coalesce — it returns `None` on empty SUM and breaks the comparison. (D-01)
- **Importing `python-telegram-bot` from `src/risk/`.** Phase 6 emits a `CircuitBreakerAlert` event via the hook surface; Phase 7 (NOTIF-03) implements delivery. Importing telegram from risk creates a phase-ordering violation. (D-13)
- **Using `aioredis` (the standalone package).** Deprecated and merged into redis-py. The project already uses `redis.asyncio` in `src/monitoring/health.py:5`. Stay consistent.
- **Setting `risk_check_passed=False` without also marking the candidate REJECTED.** The pipeline currently sets `risk_check_passed=True` unconditionally in `PipelineRunner._persist` line 184. Phase 6 must thread the `RiskDecision` back to `_persist` so both `CandidateSignalORM.status='REJECTED'` AND `ApprovedSignalORM` is NOT created for failed candidates. The contract: failed candidates do not produce an `ApprovedSignalORM` row at all (matches existing dedup/conflict/quota behavior).
- **Adding a `rejection_reason` column to `candidate_signals`.** Explicitly out of scope (D-05). Logs are sufficient for v1.
- **Logging with `id(sig)` and assuming it survives serialization.** `id()` is process-local and only meaningful within the current pipeline run. It is fine for in-process traceability but MUST NOT be persisted. Mirror the existing `status_map[id(sig)]` pattern in `PipelineRunner` (line 94).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Cross-process counter with TTL | Custom file-based counter or in-memory dict | Redis `INCR` + `SET ... EX <seconds>` | Atomic, persistent across restarts, TTL handled natively. Already plumbed in project. |
| Async Redis client | Hand-rolled connection pool | `redis.asyncio.from_url(...)` | redis-py 7.x ships production-grade async client; matches existing `health.py` pattern |
| Test double for Redis | `unittest.mock.AsyncMock` chains | `fakeredis.FakeAsyncRedis` | Real Redis state machine in-process — TTL math, INCR atomicity, key expiry all behave correctly |
| UTC-day boundary in SQL | Python datetime arithmetic + bind-param | PostgreSQL `func.date_trunc('day', func.now() AT TIME ZONE 'UTC')` | Server-side; survives clock-skew between app and DB; idiomatic SQLAlchemy 2.0 |
| Decimal arithmetic | `float` everywhere | `decimal.Decimal` for money math | `TradeORM.pnl_pct` is `Numeric(8,5)` and returns `Decimal`. Mixing `float` and `Decimal` raises `TypeError` and loses precision. Project models use `Decimal` consistently. |
| Pydantic v2 DTOs | Plain `@dataclass` | `pydantic.BaseModel` | Project standard (CLAUDE.md "Invariants"); free validation; aligned with `signal_data.py` |
| ATR computation | New ATR helper in sizer | Read `MarketRegime.atr_value` / `atr_pctile` from upstream pipeline scope | Already computed by `RegimeDetector` in step 3 of `PipelineRunner.run` |
| Telegram client | `python-telegram-bot` import in risk | Emit `CircuitBreakerAlert` DTO via hook surface | Phase ordering: Phase 7 owns Telegram (NOTIF-03). D-13. |

**Key insight:** Phase 6 is wiring + a small amount of state-machine code. Almost all heavy lifting (regime detection, ATR computation, persistence transaction, async DB engine, async Redis client, structured logging) is already done elsewhere in the codebase. Resist the urge to "improve" any of it.

## Runtime State Inventory

> **Skip rationale:** Phase 6 is a greenfield package addition (`src/risk/`), not a rename or migration. No existing runtime state needs to be relocated, renamed, or re-registered. Adding a new env var (`theoretical_equity_usd`) is the only environment change, and it has a default — no migration required.
>
> **Stored data — None.** Redis keys (`risk:cb:*`) are NEW keys; no existing data to migrate.
> **Live service config — None.** No external service config changes.
> **OS-registered state — None.** APScheduler jobs are unchanged (the existing `run_pipeline` job already runs every 15 min; the risk step lives inside `PipelineRunner.run()` which `run_pipeline` already calls).
> **Secrets/env vars — One added with default.** `THEORETICAL_EQUITY_USD=10000` (optional in `.env`; defaults to 10000 in `Settings`).
> **Build artifacts — None.** No package rename, no `.egg-info` invalidation.

## Common Pitfalls

### Pitfall 1: `atr_pctile` scale mismatch (0–1 vs 0–100)
**What goes wrong:** Sizer compares `atr_pctile=0.95` to `settings.atr_high_vol_percentile=90` and never matches → no high-vol reduction → real positions sized 30% too large in volatile markets.
**Why it happens:** AGENTS.md §12.2 pseudo-code uses `if atr_pctile >= 90` (whole-number scale), but the actual `RegimeDetector` returns 0.0–1.0. The Settings field name `atr_high_vol_percentile=90` invites direct comparison.
**How to avoid:** Always normalize: `high_threshold = settings.atr_high_vol_percentile / 100.0`. Add a unit test that explicitly verifies `atr_pctile=0.95` triggers the high-vol branch and `atr_pctile=0.05` triggers low-vol.
**Warning signs:** Tests pass with hardcoded `atr_pctile=95` but fail with `atr_pctile=0.95` (or vice versa). If you ever write `atr_pctile >= 90` in production code, that's a red flag.

### Pitfall 2: Sizer order-of-operations: cap-then-vol vs vol-then-cap
**What goes wrong:** Applying the hard cap before vol adjustment means a low-vol +30% can push past 2%. E.g., `risk_per_trade=0.01`, `hard_cap=0.02`. Cap-first: `min(0.01, 0.02)=0.01`, then `× 1.3 = 0.013` (fine). But same logic on `risk_per_trade=0.018`: cap-first `min(0.018, 0.02)=0.018`, then `× 1.3 = 0.0234` — exceeds cap. AGENTS.md §12.2 specifies vol-first.
**Why it happens:** Both orders look "right" in isolation; the spec only manifests under specific param combos. Easy to flip in a refactor.
**How to avoid:** Hardcode the order in the function body (vol → cap → concentration) and add a parameterized test: `(risk_per_trade=0.018, atr_pctile=0.05, expected_max_risk_pct=0.02)`.
**Warning signs:** Any computed `risk_pct` strictly greater than `hard_cap`.

### Pitfall 3: `func.sum()` returns `None` on empty tables, breaking `<=` comparisons
**What goes wrong:** `SELECT SUM(pnl_pct) FROM trades WHERE ...` returns `None` (Python) when no rows match. Then `None <= -0.03` raises `TypeError` in Python 3.x. RISK-01 explodes the first time it runs against an empty `TradeORM`.
**Why it happens:** SQL `SUM` of an empty set is `NULL`, which maps to Python `None`. Most folks don't test the empty case.
**How to avoid:** Always wrap: `func.coalesce(func.sum(TradeORM.pnl_pct), 0)`. Add an explicit empty-`TradeORM` unit test for every gate.
**Warning signs:** First production run after deploy crashes with `TypeError: '<=' not supported between instances of 'NoneType' and 'float'`.

### Pitfall 4: Risk gate session leaks into `_persist` transaction
**What goes wrong:** If `RiskGateRunner.evaluate` is given the `PipelineRunner._persist` session (or opens its session inside `_persist`'s `async with session.begin():` block), the read holds the transaction open longer and risks deadlock with the eventual writes. Also makes test mocking fragile.
**Why it happens:** Common naive refactor: "let's reuse the session that's already there."
**How to avoid:** `RiskGateRunner.evaluate` opens its OWN `async with AsyncSessionLocal() as session:` context for each gate (or accepts a session that the caller created BEFORE the persist transaction). Persist runs unchanged, with its own dedicated transaction.
**Warning signs:** Random test flakes mocking session behavior; `pytest` complaining about un-awaited coroutines from session usage.

### Pitfall 5: `Decimal` × `float` raises TypeError
**What goes wrong:** `equity = Decimal("10000")`; `risk_pct = 0.01` (float from settings); `equity * risk_pct` → `TypeError: unsupported operand type(s) for *: 'decimal.Decimal' and 'float'`.
**Why it happens:** `Settings.risk_per_trade` is `float` per existing code. `Settings.theoretical_equity_usd` should be `Decimal` (D-09 leans Decimal). They cannot be multiplied directly.
**How to avoid:** Always wrap: `equity * Decimal(str(risk_pct))`. NOT `Decimal(risk_pct)` directly — that captures float imprecision (`0.01 → 0.0100000000000000002...`); `str()` first preserves the literal value.
**Warning signs:** `TypeError` in sizer tests with realistic Settings values.

### Pitfall 6: Redis key namespace collision with future cache keys
**What goes wrong:** Future modules add `risk:` keys without a prefix discipline; collision corrupts breaker state.
**Why it happens:** Redis is global; namespacing is by convention only.
**How to avoid:** All breaker keys use prefix `risk:cb:`. Document in `src/risk/breaker.py` module docstring. Define constants at module top (`CB_COUNTER`, `CB_TRIPPED_AT`, `CB_COOLDOWN_UNTIL`) — no string literals scattered through methods.
**Warning signs:** Two modules `INCR`-ing the same key.

### Pitfall 7: `BreakerManager.is_tripped()` race when cooldown just expired
**What goes wrong:** Two concurrent gate evaluations both read `cooldown_until` as expired; both call `reset_if_expired()`; one wins and resets the counter, the other does the same operation and possibly clobbers a `record_stop` that occurred in between.
**Why it happens:** Pipeline is single-instance (`max_instances=1`) so this is unlikely in practice, but worth noting for the integration test.
**How to avoid:** `reset_if_expired()` is idempotent: only deletes keys if cooldown is strictly in the past. Use `MULTI/EXEC` (Redis transaction) for the reset if you want strict safety; for v1 the single-instance guarantee is sufficient.
**Warning signs:** Counter "drifts" in long-running tests (you fix this later, in Phase 8 if auto-mode introduces parallelism).

### Pitfall 8: `fakeredis` event-loop binding in pytest fixtures
**What goes wrong:** Creating a `FakeAsyncRedis` instance at module scope or in a session-scoped fixture binds it to one event loop; subsequent tests in a different loop raise `RuntimeError: <Queue > is bound to a different event loop`.
**Why it happens:** Documented `fakeredis-py` issue #292. [CITED: https://github.com/cunla/fakeredis-py/issues/292]
**How to avoid:** Use `@pytest.fixture` (function-scoped, the default) — NOT `scope="session"` or `scope="module"`. Create a fresh `FakeAsyncRedis()` per test function. Yield it, then `await fake_redis.aclose()`.
**Warning signs:** Cryptic event-loop errors when a single test passes in isolation but fails in a suite.

## Code Examples

### §1. RISK-01 daily P&L query (UTC-day boundary)
```python
# src/risk/gates.py
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.trade import TradeORM


async def evaluate_daily_loss(
    session: AsyncSession, daily_loss_limit: float
) -> tuple[bool, float]:
    """Return (passed, daily_pnl_pct).

    passed=True when daily_pnl_pct > daily_loss_limit (e.g., -0.03).
    Empty trades table → SUM is NULL → coalesce to 0.0 → passed=True.
    """
    stmt = select(
        func.coalesce(func.sum(TradeORM.pnl_pct), 0)
    ).where(
        TradeORM.closed_at >= func.date_trunc(
            "day", func.timezone("UTC", func.now())
        ),
        TradeORM.status == "CLOSED",
    )
    result = await session.execute(stmt)
    daily_pnl_pct = float(result.scalar_one())
    passed = daily_pnl_pct > daily_loss_limit
    return passed, daily_pnl_pct
```
*Source: SQLAlchemy 2.0 async docs + existing pattern in `src/pipeline/quota.py:30-36` for `func.count()`. `func.date_trunc('day', func.timezone('UTC', func.now()))` is the SQLAlchemy 2.0 idiom for the SQL in D-14.*

### §2. RISK-02 max-positions count query
```python
async def evaluate_max_positions(
    session: AsyncSession, max_positions: int
) -> tuple[bool, int]:
    """Return (passed, open_count). passed=True when open_count < max_positions."""
    stmt = select(func.count()).select_from(TradeORM).where(TradeORM.status == "OPEN")
    result = await session.execute(stmt)
    open_count = int(result.scalar_one())
    passed = open_count < max_positions
    return passed, open_count
```
*Source: matches `src/pipeline/quota.py:30-36` pattern.*

### §3. RISK-03 same-direction count
```python
async def count_same_direction_open(
    session: AsyncSession, direction: str
) -> int:
    """Return count of OPEN trades in the given direction (BUY or SELL)."""
    stmt = (
        select(func.count())
        .select_from(TradeORM)
        .where(TradeORM.status == "OPEN", TradeORM.direction == direction)
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())
```

### §4. Pydantic v2 DTO definitions (mirror `signal_data.py`)
```python
# src/risk/events.py
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PositionSizing(BaseModel):
    """Output of the ATR-based position sizer (RISK-04)."""
    model_config = ConfigDict(frozen=True)

    risk_pct: float = Field(ge=0.0)
    risk_amount_usd: Decimal
    size_lots: Decimal
    vol_factor: float
    concentration_reduced: bool


class RiskDecision(BaseModel):
    """Result of running all risk gates against one candidate."""
    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: Optional[str] = None  # gate name when not passed; None when passed
    sizing: Optional[PositionSizing] = None  # populated only when passed
    concentration_reduced: bool = False  # mirrors sizing.concentration_reduced for convenience


class CircuitBreakerAlert(BaseModel):
    """Event emitted when the breaker trips. Phase 7 NOTIF-03 consumes this."""
    model_config = ConfigDict(frozen=True)

    tripped_at: datetime
    consecutive_stops: int
    cooldown_until: datetime
    last_stop_strategy: str
    last_stop_trade_id: Optional[UUID] = None
```
*Source: pattern derived from `src/models/signal_data.py:CandidateSignal` (lines 93-119). `frozen=True` is the project convention for immutable DTOs (matches Pydantic v2 `model_config` style; `signal_data.py` itself omits `frozen=True` but the risk DTOs benefit from immutability since they are passed across phase boundaries).* [CITED: pydantic.dev/docs — `ConfigDict(frozen=True)` is the v2 way to make a model hashable/immutable]

### §5. `RiskGateRunner.evaluate` orchestration
```python
# src/risk/runner.py (sketch)
from decimal import Decimal
from typing import Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.signal_data import CandidateSignal, MarketRegime
from src.risk.breaker import BreakerManager
from src.risk.events import RiskDecision
from src.risk.gates import (
    count_same_direction_open,
    evaluate_daily_loss,
    evaluate_max_positions,
)
from src.risk.sizer import calculate_position_size

log = structlog.get_logger(__name__)


class RiskGateRunner:
    def __init__(self, breaker: BreakerManager | None = None):
        self.breaker = breaker or BreakerManager()
        self.settings = get_settings()

    async def evaluate(
        self,
        candidate: CandidateSignal,
        regime: MarketRegime,
        session: AsyncSession,  # caller-provided, read-only
    ) -> RiskDecision:
        # D-12: short-circuit if breaker is tripped
        await self.breaker.reset_if_expired()
        if await self.breaker.is_tripped():
            log.info(
                "risk.gate.rejected",
                gate="circuit_breaker",
                reason="circuit_breaker_active",
                strategy=candidate.strategy.value,
                direction=candidate.direction.value,
                entry_price=candidate.entry_price,
                signal_ref=id(candidate),
            )
            return RiskDecision(passed=False, reason="circuit_breaker_active")

        # Gate 1: daily loss
        passed, daily_pnl = await evaluate_daily_loss(session, self.settings.daily_loss_limit)
        if not passed:
            log.info("risk.gate.rejected", gate="daily_loss", reason="daily_loss_limit",
                     daily_pnl_pct=daily_pnl, strategy=candidate.strategy.value,
                     direction=candidate.direction.value, entry_price=candidate.entry_price,
                     signal_ref=id(candidate))
            return RiskDecision(passed=False, reason="daily_loss_limit")

        # Gate 2: max positions
        passed, open_count = await evaluate_max_positions(session, self.settings.max_positions)
        if not passed:
            log.info("risk.gate.rejected", gate="max_positions", reason="max_positions",
                     open_count=open_count, strategy=candidate.strategy.value,
                     direction=candidate.direction.value, entry_price=candidate.entry_price,
                     signal_ref=id(candidate))
            return RiskDecision(passed=False, reason="max_positions")

        # Gate 3: concentration (REDUCE not BLOCK per D-04)
        same_dir_count = await count_same_direction_open(session, candidate.direction.value)

        # Sizer (RISK-04)
        sizing = calculate_position_size(
            equity=self.settings.theoretical_equity_usd,
            risk_per_trade=self.settings.risk_per_trade,
            entry_price=Decimal(str(candidate.entry_price)),
            sl_price=Decimal(str(candidate.sl_price)),
            atr_value=Decimal(str(regime.atr_value)),
            atr_pctile=regime.atr_pctile,
            hard_cap=self.settings.hard_cap_risk,
            atr_high_vol_pctile=self.settings.atr_high_vol_percentile,
            atr_low_vol_pctile=self.settings.atr_low_vol_percentile,
            same_direction_open_count=same_dir_count,
        )
        log.info("risk.sizing.calculated", strategy=candidate.strategy.value,
                 direction=candidate.direction.value, risk_pct=sizing.risk_pct,
                 size_lots=str(sizing.size_lots), vol_factor=sizing.vol_factor,
                 concentration_reduced=sizing.concentration_reduced)

        return RiskDecision(
            passed=True,
            reason=None,
            sizing=sizing,
            concentration_reduced=sizing.concentration_reduced,
        )
```

### §6. `PipelineRunner.run` integration (insertion point: between line 90 and line 105 of current `src/pipeline/runner.py`)
```python
# After Step 5 (apply_quota) — current line 87-90:
approved_ranked, quota_rejected = await apply_quota(
    ranked, max_per_day=settings.max_signals_per_day
)

# *** NEW Step 5.5: Risk gates (D-03) ***
from src.risk.runner import RiskGateRunner  # top-of-file import in production

risk_runner = RiskGateRunner()
risk_passed: list[tuple[CandidateSignal, float]] = []
risk_rejected: list[CandidateSignal] = []

# Open a single read-only session for ALL risk gate reads of this pipeline cycle.
# This session is independent of the persist transaction below.
async with AsyncSessionLocal() as risk_session:
    for sig, score in approved_ranked:
        decision = await risk_runner.evaluate(sig, regime, risk_session)
        if decision.passed:
            risk_passed.append((sig, score))
        else:
            risk_rejected.append(sig)

# Replace `approved_ranked` for downstream code
approved_ranked = risk_passed

# Build status map (existing pattern, line 94-102)
status_map: dict[int, str] = {}
for sig in deduped:
    status_map[id(sig)] = "DEDUPED"
for sig in conflict_rejected:
    status_map[id(sig)] = "REJECTED"
for sig in quota_rejected:
    status_map[id(sig)] = "REJECTED"
for sig in risk_rejected:                # *** NEW ***
    status_map[id(sig)] = "REJECTED"
for sig, _ in approved_ranked:
    status_map[id(sig)] = "APPROVED"

# Step 6: Persist (unchanged — line 105)
approved_orms = await self._persist(candidates, approved_ranked, regime, status_map)
```
*Source: `src/pipeline/runner.py:43-115` current code. Insertion point is between current lines 90 (end of quota) and 92 (start of `status_map` build).*

### §7. Test scaffolding for `BreakerManager` with `fakeredis`
```python
# tests/test_risk/test_breaker.py
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from fakeredis import FakeAsyncRedis

from src.risk.breaker import BreakerManager, CB_COOLDOWN_UNTIL, CB_COUNTER


@pytest.fixture
async def fake_redis():
    """Function-scoped fakeredis to avoid event-loop binding issues (Pitfall 8)."""
    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
async def breaker(fake_redis):
    return BreakerManager(redis=fake_redis)


@pytest.mark.asyncio
async def test_breaker_starts_untripped(breaker):
    assert await breaker.is_tripped() is False


@pytest.mark.asyncio
async def test_breaker_trips_on_nth_consecutive_stop(breaker, fake_redis):
    # CIRCUIT_BREAKER_STOPS=8 in default Settings
    alerts = []
    for i in range(7):
        alerts.append(await breaker.record_stop(trade_id=uuid4(), strategy="liquidity_sweep"))
    assert all(a is None for a in alerts)
    assert await breaker.is_tripped() is False

    alert = await breaker.record_stop(trade_id=uuid4(), strategy="liquidity_sweep")
    assert alert is not None
    assert alert.consecutive_stops == 8
    assert await breaker.is_tripped() is True


@pytest.mark.asyncio
async def test_breaker_resets_on_win(breaker):
    for _ in range(3):
        await breaker.record_stop(trade_id=uuid4(), strategy="liquidity_sweep")
    await breaker.record_win()
    # Counter is now 0; next stop starts fresh
    alert = await breaker.record_stop(trade_id=uuid4(), strategy="liquidity_sweep")
    assert alert is None  # only 1 consecutive stop, far from threshold


@pytest.mark.asyncio
async def test_breaker_resets_after_cooldown(breaker, fake_redis):
    # Trip it
    for _ in range(8):
        await breaker.record_stop(trade_id=uuid4(), strategy="liquidity_sweep")
    assert await breaker.is_tripped() is True

    # Manually expire cooldown
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    await fake_redis.set(CB_COOLDOWN_UNTIL, past)

    reset = await breaker.reset_if_expired()
    assert reset is True
    assert await breaker.is_tripped() is False
    assert int(await fake_redis.get(CB_COUNTER) or 0) == 0
```

### §8. Test scaffolding for gates (mocked `AsyncSession`)
```python
# tests/test_risk/test_gates.py
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from src.risk.gates import (
    count_same_direction_open,
    evaluate_daily_loss,
    evaluate_max_positions,
)


def _mock_session(scalar_value):
    session = MagicMock()
    result = MagicMock()
    result.scalar_one = MagicMock(return_value=scalar_value)
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_daily_loss_passes_when_empty():
    """Empty TradeORM → coalesce returns 0 → passes (D-01)."""
    session = _mock_session(Decimal("0"))
    passed, pnl = await evaluate_daily_loss(session, daily_loss_limit=-0.03)
    assert passed is True
    assert pnl == 0.0


@pytest.mark.asyncio
async def test_daily_loss_blocks_at_threshold():
    """daily_pnl == -0.03 → trip (NOT strictly less; D-14 uses <=)."""
    session = _mock_session(Decimal("-0.03"))
    passed, _ = await evaluate_daily_loss(session, daily_loss_limit=-0.03)
    assert passed is False


@pytest.mark.asyncio
async def test_daily_loss_blocks_below_threshold():
    session = _mock_session(Decimal("-0.05"))
    passed, _ = await evaluate_daily_loss(session, daily_loss_limit=-0.03)
    assert passed is False


@pytest.mark.asyncio
async def test_max_positions_passes_below_threshold():
    session = _mock_session(4)
    passed, count = await evaluate_max_positions(session, max_positions=5)
    assert passed is True
    assert count == 4


@pytest.mark.asyncio
async def test_max_positions_blocks_at_threshold():
    session = _mock_session(5)
    passed, _ = await evaluate_max_positions(session, max_positions=5)
    assert passed is False
```

### §9. Sizer tests (pure function, no fixtures)
```python
# tests/test_risk/test_sizer.py
from decimal import Decimal
import pytest

from src.risk.sizer import calculate_position_size


COMMON = dict(
    equity=Decimal("10000"),
    entry_price=Decimal("2340.00"),
    sl_price=Decimal("2325.00"),
    atr_value=Decimal("15.0"),
    atr_high_vol_pctile=90,
    atr_low_vol_pctile=10,
    hard_cap=0.02,
)


def test_baseline_normal_vol_no_concentration():
    s = calculate_position_size(
        risk_per_trade=0.01, atr_pctile=0.5, same_direction_open_count=0, **COMMON
    )
    assert s.vol_factor == 1.0
    assert s.risk_pct == 0.01
    assert s.concentration_reduced is False


def test_high_vol_reduces_30_percent():
    s = calculate_position_size(
        risk_per_trade=0.01, atr_pctile=0.95, same_direction_open_count=0, **COMMON
    )
    assert s.vol_factor == 0.7
    assert s.risk_pct == pytest.approx(0.007)


def test_low_vol_increases_30_percent():
    """D-16: low-vol +30% IS in scope, not just high-vol -30%."""
    s = calculate_position_size(
        risk_per_trade=0.01, atr_pctile=0.05, same_direction_open_count=0, **COMMON
    )
    assert s.vol_factor == 1.3
    assert s.risk_pct == pytest.approx(0.013)


def test_hard_cap_clamps_after_low_vol_bump():
    """D-07: vol bump can never escape the cap."""
    s = calculate_position_size(
        risk_per_trade=0.018, atr_pctile=0.05, same_direction_open_count=0, **COMMON
    )
    # 0.018 × 1.3 = 0.0234 → capped at 0.02
    assert s.vol_factor == 1.3
    assert s.risk_pct == pytest.approx(0.02)


def test_concentration_halves_after_cap():
    """D-07: concentration applies AFTER hard cap."""
    s = calculate_position_size(
        risk_per_trade=0.018, atr_pctile=0.05, same_direction_open_count=4, **COMMON
    )
    # 0.018 × 1.3 = 0.0234 → cap → 0.02 → ×0.5 → 0.01
    assert s.risk_pct == pytest.approx(0.01)
    assert s.concentration_reduced is True


def test_atr_pctile_scale_invariant():
    """D-07: 0.95 is high-vol; 95 (whole-number) would be wrong."""
    s_correct = calculate_position_size(
        risk_per_trade=0.01, atr_pctile=0.95, same_direction_open_count=0, **COMMON
    )
    assert s_correct.vol_factor == 0.7
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `aioredis` standalone package | `redis.asyncio` from `redis-py>=4.2` | redis-py 4.2.0 (Mar 2022) — `aioredis-py` archived | Use `import redis.asyncio as aioredis` (existing project pattern in `src/monitoring/health.py:5`). Do NOT add `aioredis` as a dep. [CITED: https://github.com/redis/redis-py — release notes] |
| `fakeredis.aioredis.FakeRedis` (legacy) | `fakeredis.FakeAsyncRedis` (>= 2.x) | fakeredis 2.0 (2022) | Import path is `from fakeredis import FakeAsyncRedis`. Old `fakeredis.aioredis` paths still work but are not the documented current API. [CITED: https://fakeredis.readthedocs.io/] |
| Pydantic v1 `Config` inner class | Pydantic v2 `model_config = ConfigDict(...)` | Pydantic 2.0 (Jun 2023) | Project is already on v2 (`signal_data.py` uses Pydantic v2 `Field`); follow the v2 idiom. |
| SQLAlchemy 1.x `Column()` + `query()` | SQLAlchemy 2.0 `mapped_column` + `select()` | SQLAlchemy 2.0 (Jan 2023) | Project is already on 2.0; mirror existing `src/pipeline/quota.py` async query style. |

**Deprecated/outdated:**
- `aioredis` standalone — superseded; do not use.
- `fakeredis-aiohttp` — was a separate add-on; modern `fakeredis` includes async support directly.
- `Decimal(float_value)` — captures float imprecision; use `Decimal(str(float_value))`.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | XAUUSD lot conversion: 1 lot = 100 oz, so `size_lots = risk_amount / (sl_distance × 100)` | Code Examples §sizer | Wrong lot size in production. AGENTS.md §12.2 explicitly states this; treating as solid but worth re-confirming with broker contract specs (the project plans IG demo → live; IG XAUUSD CFD has different contract sizing — typically 1 contract = 100 oz, but verify before Phase 8). [ASSUMED — sourced from AGENTS.md §12.2] |
| A2 | `func.date_trunc('day', func.timezone('UTC', func.now()))` is the SQLAlchemy 2.0 idiom for the Postgres SQL in D-14 | Code Examples §1 | Query may not produce the intended UTC-day boundary. Recommend a smoke-test against a populated test DB to confirm. [ASSUMED — derived from SQLAlchemy generic `func` mapping; the equivalent raw SQL in D-14 is verified] |
| A3 | `RISK-01 trip condition` uses `<=` (D-14 confirms) → at exactly -3.000% the gate trips | Validation Architecture | A `<` vs `<=` flip silently changes which boundary blocks. D-14 explicitly uses `<=`; tests must pin this. [VERIFIED: CONTEXT.md D-14] |
| A4 | `RISK-02 trip condition` uses `>=` (open count ≥ MAX_POSITIONS) | Validation Architecture | Same boundary risk. ROADMAP success criterion §2 says "5 or more" → `>=`. [VERIFIED: ROADMAP Phase 6 §2] |
| A5 | Order of breaker check vs gates: breaker FIRST (D-12 explicit), no DB read if tripped | Code Examples §5 | Wasted DB reads when tripped (acceptable but suboptimal); inconsistent if reordered. [VERIFIED: CONTEXT.md D-12] |
| A6 | `BreakerManager` is constructed once per `RiskGateRunner` instance; `RiskGateRunner` is constructed inside `PipelineRunner.run()` (per-run instance) | Code Examples §6 | If `RiskGateRunner` is hoisted to module-level, the `redis.asyncio` connection is shared across pipeline runs. Acceptable (redis-py async client is connection-pool-aware) but be deliberate about lifecycle. [ASSUMED — pattern; either choice works, recommend per-run for consistency with existing PipelineRunner pattern] |
| A7 | Phase 7 will register a `BreakerAlertHook` at startup; until then, the hook list is empty and `_publish_alert` is a no-op | Code Examples §4 | If Phase 6 ships and a stop sequence trips the breaker before Phase 7, no Telegram alert fires (only structlog). This is acceptable per D-13 (deferred delivery). [VERIFIED: CONTEXT.md D-13] |
| A8 | `pyproject.toml` should add `fakeredis>=2.20` to main `[project.dependencies]` (not optional-dependencies) | Standard Stack | Pollutes prod image with test-only dep (~100KB). Project precedent (pytest, respx, aiosqlite already in main deps) supports it; cleaner alternative is to add `[project.optional-dependencies.test]`. [ASSUMED — project precedent; either works] |

## Open Questions

1. **Should `RiskGateRunner.evaluate` accept a session or open its own?**
   - What we know: D-04 of Phase 4 requires single transaction at persist; risk reads must NOT be in that transaction.
   - What's unclear: Whether to open ONE session for the entire pipeline cycle (pass into `evaluate()`) or open a new session per gate call.
   - Recommendation: Open one read-only session in `PipelineRunner.run()`, pass it to `RiskGateRunner.evaluate()` for each candidate. Closes after the loop, before `_persist` opens its own transaction. This matches the integration sketch in Code Examples §6.

2. **Where does Phase 6 wire `register_alert_hook`?**
   - What we know: Phase 7 will call it (D-13).
   - What's unclear: Phase 6 deliverables include the function; Phase 7 deliverables include calling it. Phase 6 needs to test the hook plumbing.
   - Recommendation: Phase 6 ships `register_alert_hook` and a unit test that registers a fake hook + verifies it gets called when the breaker trips. Wiring from `src/main.py` is Phase 7 work.

3. **Should `theoretical_equity_usd` be Decimal or float in `Settings`?**
   - What we know: Project leans Decimal for money fields in models.
   - What's unclear: Pydantic Settings + `.env` parsing — Pydantic v2 supports `Decimal` as a field type, but env vars are strings. `Decimal("10000")` is the canonical default.
   - Recommendation: Decimal. Add a settings test confirming `Settings().theoretical_equity_usd == Decimal("10000")` and that `Settings(theoretical_equity_usd="20000").theoretical_equity_usd == Decimal("20000")`.

4. **Health endpoint `daily_pnl_pct` uses gate query — refactor concern?**
   - What we know: `evaluate_daily_loss` returns `(passed, daily_pnl_pct)`.
   - What's unclear: Should `health.py` import from `src/risk/gates.py` (creating a dep), or should the query helper move to a shared location?
   - Recommendation: Export a thin `async def get_daily_pnl_pct(session) -> float` from `src/risk/gates.py`; both `evaluate_daily_loss` and `health.py` consume it. No circular imports (health.py already imports from `src.config`, `src.database`).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Redis (live, for one integration test) | `BreakerManager` integration test | ✓ | 7-alpine (docker-compose) | `fakeredis` covers all unit tests; integration test can be skipped on CI without Docker |
| PostgreSQL (live, for one integration test) | Pipeline integration test | ✓ | 16-alpine (docker-compose) | Mock `AsyncSession.execute()` for unit tests |
| `redis` python package | `BreakerManager` runtime + tests | ✓ | 7.1.0 [VERIFIED: `pip show redis`] | — |
| `fakeredis` python package | `BreakerManager` unit tests | ✗ (NOT in pyproject.toml) | — | MUST add `fakeredis>=2.20` to `pyproject.toml` |
| `sqlalchemy[asyncio]` | All gate queries | ✓ | >=2.0.0 [VERIFIED: pyproject.toml:11] | — |
| `pydantic-settings` | New `theoretical_equity_usd` field | ✓ | >=2.3.0 [VERIFIED: pyproject.toml:16] | — |
| `pytest-asyncio` | All async tests | ✓ | >=0.23.0 with `asyncio_mode = "auto"` [VERIFIED: pyproject.toml:27,38] | — |

**Missing dependencies with no fallback:**
- None blocking.

**Missing dependencies with fallback:**
- `fakeredis>=2.20` — must be added to `pyproject.toml` before `BreakerManager` unit tests can run. Plan should include a "dependency add" task as Wave 0.

## Validation Architecture

> Required by Nyquist Dimension 8 (workflow.nyquist_validation: true in `.planning/config.json`).

### Test Framework

| Property | Value |
|----------|-------|
| Framework | `pytest>=8.2.0` + `pytest-asyncio>=0.23.0` (already installed; pyproject.toml:26-27) |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options] asyncio_mode = "auto"`) — no separate `pytest.ini` |
| Quick run command | `pytest tests/test_risk/ -x -q` |
| Full suite command | `pytest -q` |
| Test layout | `tests/test_risk/` (NEW; mirror `tests/test_pipeline/`) |
| Required env vars | Already set by `tests/conftest.py:11-13` — `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? | Falsifiable Property (proves req if true; false ⇒ req broken) |
|--------|----------|-----------|-------------------|-------------|---------------------------------------------------------------|
| RISK-01 | Daily P&L ≤ -3% blocks signal as REJECTED | unit | `pytest tests/test_risk/test_gates.py::test_daily_loss_blocks_at_threshold -x` | ❌ Wave 0 | At `daily_pnl_pct == -0.03`, `evaluate_daily_loss` returns `passed=False`; at `daily_pnl_pct == -0.029`, returns `passed=True` |
| RISK-01 | Empty TradeORM passes the gate (D-01 invariant) | unit | `pytest tests/test_risk/test_gates.py::test_daily_loss_passes_when_empty -x` | ❌ Wave 0 | When `func.coalesce(func.sum(...), 0)` returns `0`, `passed=True` and `daily_pnl_pct == 0.0`; never raises `TypeError` from `None <= -0.03` |
| RISK-01 | Pipeline marks RISK-01-rejected candidate as `CandidateSignalORM.status='REJECTED'` and creates NO `ApprovedSignalORM` | integration | `pytest tests/test_risk/test_pipeline_integration.py::test_risk01_rejection_propagates_to_status_map -x` | ❌ Wave 0 | After `PipelineRunner.run()` with a candidate whose RISK-01 fails, `session.add` is called for `CandidateSignalORM(status='REJECTED')` but NOT for any `ApprovedSignalORM` referencing that candidate |
| RISK-01 | structlog event `risk.gate.rejected` emitted with `gate='daily_loss'` and `daily_pnl_pct` field | unit | `pytest tests/test_risk/test_runner.py::test_daily_loss_emits_structured_log -x` | ❌ Wave 0 | Captured log records contain exactly one event with `event='risk.gate.rejected'`, `gate='daily_loss'`, `reason='daily_loss_limit'`, and a numeric `daily_pnl_pct` field |
| RISK-02 | 5+ open positions blocks new signal | unit | `pytest tests/test_risk/test_gates.py::test_max_positions_blocks_at_threshold -x` | ❌ Wave 0 | At `open_count == 5`, `evaluate_max_positions` returns `passed=False`; at `open_count == 4`, returns `passed=True` |
| RISK-02 | Empty TradeORM passes the gate | unit | `pytest tests/test_risk/test_gates.py::test_max_positions_passes_when_empty -x` | ❌ Wave 0 | When `count == 0`, `passed=True` |
| RISK-03 | 4+ same-direction open positions sets `concentration_reduced=True` and HALVES `risk_pct` (does NOT block per D-04) | unit | `pytest tests/test_risk/test_sizer.py::test_concentration_halves_after_cap -x` | ❌ Wave 0 | With `same_direction_open_count=4`, returned `PositionSizing.concentration_reduced is True` AND `risk_pct == 0.5 × (post-cap risk_pct)` |
| RISK-03 | Concentration does NOT cause `RiskDecision.passed=False` | unit | `pytest tests/test_risk/test_runner.py::test_concentration_passes_with_reduced_size -x` | ❌ Wave 0 | With 4 same-direction OPEN trades, `RiskDecision.passed is True` AND `RiskDecision.concentration_reduced is True` |
| RISK-04 | High-vol regime reduces risk by 30% (vol_factor=0.7) | unit | `pytest tests/test_risk/test_sizer.py::test_high_vol_reduces_30_percent -x` | ❌ Wave 0 | `atr_pctile=0.95` ⇒ `vol_factor == 0.7` ⇒ `risk_pct == 0.7 × risk_per_trade` |
| RISK-04 | Low-vol regime increases risk by 30% (vol_factor=1.3) — D-16 | unit | `pytest tests/test_risk/test_sizer.py::test_low_vol_increases_30_percent -x` | ❌ Wave 0 | `atr_pctile=0.05` ⇒ `vol_factor == 1.3` ⇒ `risk_pct == 1.3 × risk_per_trade` (subject to cap) |
| RISK-04 | Hard cap (2%) applied AFTER vol adjustment, never escaped | unit | `pytest tests/test_risk/test_sizer.py::test_hard_cap_clamps_after_low_vol_bump -x` | ❌ Wave 0 | For ANY input, `PositionSizing.risk_pct <= hard_cap × (0.5 if concentration_reduced else 1.0)` |
| RISK-04 | atr_pctile scale invariant — sizer correctly handles 0.0–1.0 input (NOT 0–100) | unit | `pytest tests/test_risk/test_sizer.py::test_atr_pctile_scale_invariant -x` | ❌ Wave 0 | `atr_pctile=0.95` triggers high-vol; `atr_pctile=0.05` triggers low-vol; `atr_pctile=0.5` triggers normal |
| RISK-04 | size_lots > 0 for valid inputs; size_lots == 0 when sl_distance == 0 | unit | `pytest tests/test_risk/test_sizer.py::test_size_lots_zero_when_no_sl_distance -x` | ❌ Wave 0 | Given `entry_price == sl_price`, returns `size_lots == Decimal("0")` |
| RISK-05 | 8 consecutive stops trip the breaker (returns `CircuitBreakerAlert`) | unit | `pytest tests/test_risk/test_breaker.py::test_breaker_trips_on_nth_consecutive_stop -x` | ❌ Wave 0 | First 7 `record_stop()` calls return `None`; 8th returns a `CircuitBreakerAlert` with `consecutive_stops=8`; `is_tripped()` becomes `True` |
| RISK-05 | While tripped, gate runner short-circuits ALL candidates with reason `circuit_breaker_active` | unit | `pytest tests/test_risk/test_runner.py::test_breaker_short_circuits_all_gates -x` | ❌ Wave 0 | When `BreakerManager.is_tripped()` returns True, `RiskGateRunner.evaluate()` returns `RiskDecision(passed=False, reason='circuit_breaker_active')` AND does NOT call any of the 3 gate functions or the sizer |
| RISK-05 | Cooldown is exactly `circuit_breaker_cooldown_hours × 3600` seconds | unit | `pytest tests/test_risk/test_breaker.py::test_breaker_cooldown_ttl -x` | ❌ Wave 0 | After trip, `redis.ttl('risk:cb:cooldown_until')` is within ±2 seconds of `circuit_breaker_cooldown_hours × 3600` |
| RISK-05 | Counter resets on first winning trade | unit | `pytest tests/test_risk/test_breaker.py::test_breaker_resets_on_win -x` | ❌ Wave 0 | After `record_win()`, `redis.get('risk:cb:consecutive_stops') == "0"` |
| RISK-05 | Counter resets when cooldown expires | unit | `pytest tests/test_risk/test_breaker.py::test_breaker_resets_after_cooldown -x` | ❌ Wave 0 | After cooldown timestamp passes, `reset_if_expired()` returns True; `is_tripped()` returns False; counter is 0 |
| RISK-05 | `CircuitBreakerAlert` is published through `_alert_hooks` (Phase 7 contract) | unit | `pytest tests/test_risk/test_runner.py::test_alert_hook_invoked_on_trip -x` | ❌ Wave 0 | A registered fake hook is called exactly once with the `CircuitBreakerAlert` when the breaker trips |
| RISK-05 | RISK-01 (daily loss) does NOT trip the breaker (D-15) | unit | `pytest tests/test_risk/test_runner.py::test_daily_loss_does_not_trip_breaker -x` | ❌ Wave 0 | After RISK-01 rejection, `redis.get('risk:cb:consecutive_stops')` is unchanged (no INCR called) |
| RISK-05 (integration) | Real Redis from docker-compose works end-to-end | integration | `pytest tests/test_risk/test_breaker_redis_integration.py -x` (requires `docker compose up redis`) | ❌ Wave 0 | Same trip behavior as fakeredis test, run against `redis://localhost:6379/0` |
| Pipeline integration | Risk step inserts between quota and persist; rejected candidates do not produce ApprovedSignalORM | integration | `pytest tests/test_risk/test_pipeline_integration.py::test_risk_rejection_no_approved_orm -x` | ❌ Wave 0 | After `PipelineRunner.run()` with all-rejected candidates, returned `list[ApprovedSignalORM]` is empty AND `status_map[id(sig)] == 'REJECTED'` for each candidate |
| Health wiring | `/health` exposes real `circuit_breaker`, `open_positions`, `daily_pnl_pct` | integration | `pytest tests/test_monitoring/test_health.py::test_health_exposes_real_risk_state -x` | ❌ Wave 0 | `GET /health` JSON response includes non-placeholder values for `circuit_breaker` (bool from `BreakerManager.is_tripped()`), `open_positions` (int from gate query), `daily_pnl_pct` (float from gate query) |
| Settings | `theoretical_equity_usd` env var loads correctly | unit | `pytest tests/test_config/test_settings.py::test_theoretical_equity_default_and_override -x` | ❌ Wave 0 | `Settings()` (no env override) → `theoretical_equity_usd == Decimal("10000")`; `THEORETICAL_EQUITY_USD=20000` → `Decimal("20000")` |

### Sampling Rate

- **Per task commit:** `pytest tests/test_risk/ -x -q`
- **Per wave merge:** `pytest -q` (full suite — risk tests + entire existing suite to catch pipeline regressions)
- **Phase gate:** Full suite green before `/gsd-verify-work`. Plus the integration tests against live Redis + Postgres (one-off manual run with `docker compose up`).

### Wave 0 Gaps

- [ ] `tests/test_risk/__init__.py` — empty package marker (mirror `tests/test_pipeline/__init__.py`)
- [ ] `tests/test_risk/conftest.py` — function-scoped `fake_redis` and `breaker` fixtures (Pitfall 8)
- [ ] `tests/test_risk/test_sizer.py` — pure-function tests, no fixtures needed
- [ ] `tests/test_risk/test_gates.py` — `_mock_session(scalar_value)` helper + 9 tests (3 per gate × empty/below/at-threshold)
- [ ] `tests/test_risk/test_breaker.py` — 7 tests covering trip, reset-on-win, reset-on-cooldown, TTL, idempotency
- [ ] `tests/test_risk/test_runner.py` — orchestration tests with mocked `BreakerManager` + mocked gates + mocked sizer; covers short-circuit, hook invocation, structured logging
- [ ] `tests/test_risk/test_pipeline_integration.py` — `PipelineRunner` end-to-end with risk step; mock all DB sessions and the breaker
- [ ] `tests/test_risk/test_breaker_redis_integration.py` — single integration test against real Redis (skip if no Docker)
- [ ] `tests/test_monitoring/test_health.py` — likely doesn't exist; verify and create
- [ ] `tests/test_config/test_settings.py` — likely doesn't exist; verify and create (or co-locate the `theoretical_equity_usd` test in `tests/test_risk/test_settings.py`)
- [ ] `pyproject.toml` — add `fakeredis>=2.20` to `[project.dependencies]`

### Manual-only verifications

- Live Redis happy-path: `docker compose up redis`, run breaker integration test, observe `redis-cli` keys `risk:cb:*` after a trip.
- `/health` smoke test: `docker compose up`, hit `GET /health`, verify `circuit_breaker: false`, `open_positions: 0`, `daily_pnl_pct: 0.0` on empty DB.

## Sources

### Primary (HIGH confidence)
- **CONTEXT.md** D-01 through D-16 — locked decisions [VERIFIED: read in this session]
- **AGENTS.md §12.1, §12.2, §12.3** — canonical risk spec [VERIFIED: read in this session]
- **`src/config.py:56-65`** — existing risk env vars [VERIFIED]
- **`src/pipeline/runner.py:43-115`** — pipeline integration point [VERIFIED]
- **`src/monitoring/health.py:5,45-49`** — `redis.asyncio` usage pattern [VERIFIED]
- **`src/pipeline/quota.py:22-36`** — async DB count query pattern [VERIFIED]
- **`src/backtesting/regime_detector.py:50,196-229`** — `atr_pctile` 0.0–1.0 scale [VERIFIED]
- **`src/models/trade.py`** — read-side schema [VERIFIED]
- **`src/models/signal_data.py:93-119`** — Pydantic v2 DTO pattern [VERIFIED]
- **`pyproject.toml`** — installed deps [VERIFIED]
- **`tests/test_pipeline/test_quota.py`, `test_runner.py`** — test idiom for mocked async sessions [VERIFIED]
- **`tests/conftest.py`** — env-var setup before src.* imports [VERIFIED]

### Secondary (MEDIUM confidence)
- [redis-py async docs (`redis.asyncio`)](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html) — official async API since redis-py 4.2
- [fakeredis-py docs](https://fakeredis.readthedocs.io/) — `FakeAsyncRedis` import path
- [fakeredis releases](https://github.com/cunla/fakeredis-py/releases) — version compatibility
- [Pydantic v2 ConfigDict](https://docs.pydantic.dev/latest/api/config/) — `frozen=True` for immutable DTOs
- [SQLAlchemy 2.0 async docs](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) — `AsyncSession`, `func.coalesce`, `select` patterns

### Tertiary (LOW confidence — flagged for validation)
- [fakeredis issue #292 — event-loop binding](https://github.com/cunla/fakeredis-py/issues/292) — confirms Pitfall 8 (use function-scoped fixtures)
- XAUUSD lot conversion (1 lot = 100 oz) — sourced from AGENTS.md §12.2; broker contract specs should be re-verified before Phase 8 (auto mode)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already installed except `fakeredis`; redis-py and SQLAlchemy 2.0 patterns verified in existing code.
- Architecture: HIGH — every integration point (config, pipeline runner, health, regime detector, trade ORM, async session) is already implemented and read in this session.
- Pitfalls: HIGH — pitfalls 1, 2, 3, 5 are project-specific and grounded in the actual code (`atr_pctile` scale verified in `regime_detector.py:50`; `Decimal`/`float` mix verified by reading `TradeORM.pnl_pct`); pitfall 8 cited from upstream issue tracker.
- Validation Architecture: HIGH — every test maps to a specific requirement with a falsifiable assertion; commands are runnable as written.
- Sizer math: HIGH — derived from AGENTS.md §12.2 with locked-in order-of-operations from D-07.
- Lot conversion (A1): MEDIUM — broker contract specs not verified in this session; sufficient for theoretical tracking (mode signal) but should be reconfirmed before mode auto.

**Research date:** 2026-04-26
**Valid until:** 2026-05-26 (stable stack; only `fakeredis` is fast-moving and pinned to `>=2.20`)
