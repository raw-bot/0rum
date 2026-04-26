# Phase 6: Risk Management - Context

**Gathered:** 2026-04-26
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 6 introduces a new `src/risk/` package that:
1. Evaluates three pre-execution risk gates (RISK-01 daily loss, RISK-02 max positions, RISK-03 concentration) on every quota-survivor candidate signal.
2. Provides a pure ATR-based position sizer with symmetric volatility adjustment (RISK-04) and a 2% hard cap.
3. Manages a Redis-backed circuit breaker (RISK-05) that trips after 8 consecutive theoretical stops, enforces a 24 h cooldown, and emits a typed alert event for Phase 7 to deliver via Telegram.

What this phase is NOT:
- It does not build the theoretical-trade lifecycle (open → TP1 → close → P&L). That is Phase 7 (SIG-02). Phase 6 reads `TradeORM` where rows exist and degrades gracefully when the table is empty.
- It does not implement Telegram delivery. `src/monitoring/telegram_bot.py` is still Phase 7 / NOTIF-03. Phase 6 emits a structured event + structlog record only.
- It does not refactor `src/pipeline/runner.py` beyond adding a single new step that delegates to `src/risk/` via a clean interface.
- It does not add scheduler jobs — gates run inline in the pipeline.
- It does not address auto-mode (real broker fills). Auto mode is Phase 8/9.

</domain>

<decisions>
## Implementation Decisions

### Theoretical-Position Data Source
- **D-01:** `TradeORM` is the single read-only source of truth for "open positions", "daily P&L", and "consecutive stops". Phase 6 NEVER inserts or updates `TradeORM` rows — Phase 7 (SIG-02) will. When `TradeORM` is empty (the normal state until Phase 7 ships), every count returns 0 and gates pass; this is intentional and tests must cover both populated and empty states.
- **D-14:** Daily P&L formula:
  ```sql
  SELECT COALESCE(SUM(pnl_pct), 0)
    FROM trades
   WHERE closed_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC')
     AND status = 'CLOSED'
  ```
  Open trades do NOT contribute (no mark-to-market). RISK-01 trip condition: `daily_pnl_pct <= -0.03` (i.e., `daily_pnl_pct <= settings.daily_loss_limit`).
- **D-09:** Equity baseline = new env var `theoretical_equity_usd` (default `10000`) added to `src/config.py`. Used by the sizer to convert `risk_pct` → `size_lots`. Dynamic equity (apply daily realized P&L) is deferred — constant baseline keeps Phase 6 self-contained.

### Module Location & Pipeline Integration
- **D-02:** New package `src/risk/` houses ALL risk logic. `src/pipeline/runner.py` calls a single public interface (e.g., `RiskGateRunner.evaluate(candidate, regime, h1_candles, session) -> RiskDecision`) and never imports gate-internal helpers. Pipeline does NOT absorb gate logic.
- **D-03:** Risk gates run as a NEW pipeline step inserted between the existing `quota` step (step 5) and `_persist` (step 6). The new step is conceptually `step 5.5: risk gates`. Quota stays exactly as-is — `MAX_SIGNALS_PER_DAY` (5 signals/calendar UTC day) is semantically distinct from RISK-02's `max_positions` (5 concurrent open theoretical positions). Both stay in place.
- **D-05:** Rejection auditing = structlog only in Phase 6. The status of a risk-rejected `CandidateSignalORM` stays `REJECTED` (existing enum); the reason is captured in a structured log event `risk.gate.rejected` with fields `gate`, `reason`, `strategy`, `direction`, `entry_price`, and optionally an in-memory `signal_ref=id(sig)`. A database `candidate_id` is not available before `_persist()` flushes ORM rows. Adding a `rejection_reason` column to `candidate_signals` is deferred — low ROI for v1; logs are sufficient.

### RISK-03 Concentration Semantics (override of REQUIREMENTS.md text)
- **D-04:** RISK-03 REDUCES the new trade size by 50% when 4+ positions are open in the same direction. It does NOT block. This aligns with `AGENTS.md` §12.1 Gate 3 ("4+ positions même direction → réduire la taille du nouveau trade de 50%"). The `REQUIREMENTS.md` wording "blocks signals that would over-expose the same direction" is overridden by AGENTS.md and explicit user direction. The signal still passes the gate (`risk_check_passed=True`); the sizer downstream halves the lot size. Reflected in the gate decision payload, not via REJECTED status.

### ATR Sizing (RISK-04)
- **D-06:** ATR window/timeframe = ATR(14) on H1 candles. This matches what existing strategies use and what `RegimeDetector.detect()` already computes. The sizer DOES NOT recompute ATR — it consumes `MarketRegime.atr_value` and `MarketRegime.atr_pctile` already in pipeline scope (the regime is detected in step 3, before the new risk step).
- **D-07:** Symmetric volatility scaling per AGENTS.md §12.2:
  - `atr_pctile >= atr_high_vol_percentile / 100` → multiply `risk_pct` by **0.7**
  - `atr_pctile <= atr_low_vol_percentile / 100` → multiply `risk_pct` by **1.3**
  - else → multiply by **1.0**

  Important scale invariant: existing `MarketRegime.atr_pctile` and `RegimeDetector._calculate_atr_percentile()` use a **0.0–1.0** percentile rank (`0.90` = 90th percentile). Existing settings use whole-number percentiles (`atr_high_vol_percentile=90`, `atr_low_vol_percentile=10`). The sizer must normalize settings to `0.90` / `0.10` before comparing. Do NOT compare `atr_pctile` directly to `90` or `10`.

  Order of operations: `risk_pct = risk_per_trade × vol_factor`, then `risk_pct = min(risk_pct, hard_cap_risk)` (the 2% hard cap, applied AFTER vol adjustment so a high-vol bump can never escape the cap), then `risk_pct *= 0.5` if the concentration condition triggered (D-04). The hard cap is the maximum risk for a non-concentrated trade; concentration further halves whatever survived the cap.
- **D-16:** RISK-04 success criterion in ROADMAP.md only mentions "reduced 30% in high-vol regimes". The symmetric "+30% in low-vol" branch IS in scope (per AGENTS.md §12.2 and explicit user confirmation). The verifier should not treat the low-vol branch as scope creep.
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
  Returns a `PositionSizing` Pydantic DTO with `risk_pct`, `risk_amount_usd`, `size_lots`, `vol_factor`, `concentration_reduced` (bool). Phase 6 does NOT add `size_lots` to `ApprovedSignalORM` — Phase 7 will decide where to persist the result (likely `TradeORM.size_lots`, which already exists). Phase 6 logs the sizing breakdown via structlog so signal-mode operators can inspect it.

### Circuit Breaker (RISK-05)
- **D-10:** State lives in Redis under the `risk:cb:` prefix:
  - `risk:cb:consecutive_stops` (int counter, no TTL)
  - `risk:cb:tripped_at` (ISO-8601 timestamp; absent when not tripped)
  - `risk:cb:cooldown_until` (ISO-8601 timestamp; written when the breaker trips, with Redis TTL = `circuit_breaker_cooldown_hours × 3600` so the keys expire automatically)
- **D-11:** Phase 6 exposes `BreakerManager` with:
  - `is_tripped() -> bool` (true iff `tripped_at` set AND `cooldown_until` in the future; reads existing Redis keys without mutating)
  - `record_stop(trade_id, strategy) -> CircuitBreakerAlert | None` (Phase 7 calls this when a `TradeORM` row closes with `close_reason = 'SL'`; returns the alert event ONLY when this stop is the trip)
  - `record_win() -> None` (Phase 7 calls this on any closed-with-profit trade; resets the counter to 0 per AGENTS.md §12.3)
  - `reset_if_expired() -> bool` (called at the start of every gate evaluation; deletes `tripped_at` / `cooldown_until` and resets `consecutive_stops` to 0 if cooldown elapsed; idempotent)
  Reset rule per AGENTS.md §12.3: counter resets on the **first winning trade** OR **end of cooldown**.
- **D-12:** While the breaker `is_tripped()`, the gate evaluator short-circuits ALL candidates to `REJECTED` with reason `circuit_breaker_active`. No daily-loss / max-positions / concentration evaluation runs. The signal is REJECTED before the sizer is invoked.
- **D-15:** RISK-01 (daily loss limit) does NOT trip the circuit breaker in Phase 6. AGENTS.md §12.1 Gate 1 includes "+ circuit breaker alert" but this is not in the ROADMAP success criterion and the user did not confirm it explicitly. Phase 6 implements RISK-01 strictly per ROADMAP: block + structlog event, no breaker trip. Tying daily-loss to the breaker is deferred (cheap to add later if needed).

### Telegram Alert Delivery (deferred to Phase 7)
- **D-13:** Phase 6 defines `CircuitBreakerAlert` (Pydantic v2 DTO in `src/risk/events.py`) with fields:
  - `tripped_at: datetime`
  - `consecutive_stops: int`
  - `cooldown_until: datetime`
  - `last_stop_strategy: str`
  - `last_stop_trade_id: UUID | None`

  When `BreakerManager.record_stop()` returns a non-None alert, Phase 6 emits structlog `risk.circuit_breaker.tripped` AND publishes the alert through a simple in-process hook interface (e.g., `BreakerAlertHook` callable list) that Phase 7's Telegram NOTIF-03 will register against. **No `python-telegram-bot` import in Phase 6**. `src/monitoring/telegram_bot.py` stays unimplemented.

### Mode Coverage
- **D-13b:** Phase 6 targets signal mode only. "Consecutive stops" = theoretical stops (`TradeORM.close_reason = 'SL'` AND `status = 'CLOSED'`). Use the existing AGENTS.md / schema convention (`SL`, `TP1`, `TP2`, `TRAIL`, `MANUAL`, `CIRCUIT_BREAKER`) rather than introducing `sl_hit`. Auto-mode broker-fill stops will be wired in Phase 8/9; the breaker contract designed here is mode-agnostic and will not need changes.

### Claude's Discretion

- Internal structure of `src/risk/` (single module vs split into `gates.py`, `sizer.py`, `breaker.py`, `events.py`, `runner.py`). Recommended: split, mirroring `src/pipeline/` structure.
- Whether `RiskGateRunner.evaluate` lives in `src/risk/runner.py` or `src/pipeline/risk.py`. Recommended: `src/risk/runner.py` so `pipeline/runner.py` only imports from `src.risk`.
- Whether unit tests use `fakeredis` (lightweight, no Docker) or a live Redis fixture. Recommended: `fakeredis-aiohttp` or `fakeredis.aioredis` for unit tests; reserve a single integration test against the real Redis from `docker-compose.yml` for the breaker keys.
- Exact `RiskDecision` schema (e.g., `passed: bool`, `reason: str | None`, `sizing: PositionSizing | None`, `concentration_reduced: bool`).
- Logging key naming convention (`risk.gate.daily_loss_limit.rejected` vs flat `risk.gate.rejected` with `gate=` field). Either works.
- Whether `theoretical_equity_usd` is a `Decimal` or `float` in Settings (project leans Decimal in models).
- Test fixture strategy for empty-TradeORM case (mock `AsyncSession.execute` returning empty list vs a populated test DB). Recommended: mock at the session level for unit tests; one integration test against real DB.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Primary Risk Spec (single source of truth for formulas + thresholds)
- `AGENTS.md` §12.1 — The 3 gates (daily loss, position limits, concentration). **Note the user override:** Gate 3 = REDUCE 50%, NOT block (D-04).
- `AGENTS.md` §12.2 — ATR-based position sizing reference implementation. Symmetric high/low-vol scaling lives here (D-07).
- `AGENTS.md` §12.3 — Circuit breaker behavior, Redis state, reset rules.
- `AGENTS.md` §10.4 — Pipeline ordering context (where the new risk step fits).

### Project Constraints
- `CLAUDE.md` (root) — Current scope, invariants, "Not implemented yet: src/risk/" line, safe modification rules. Note "Redis is only used in health.py right now" — Phase 6 will be the first runtime consumer.
- `.planning/PROJECT.md` — Risk parameters are env vars (never optimized); Redis chosen for circuit breaker state.
- `.planning/REQUIREMENTS.md` — RISK-01..RISK-05 (with concentration override per D-04).
- `.planning/ROADMAP.md` — Phase 6 success criteria (5 items).

### Existing Code to Read Before Implementing
- `src/config.py` lines 56–65 — All risk env vars already present: `risk_per_trade`, `daily_loss_limit`, `max_positions`, `circuit_breaker_stops`, `circuit_breaker_cooldown_hours`, `atr_high_vol_percentile`, `atr_low_vol_percentile`, `hard_cap_risk`. Add `theoretical_equity_usd: Decimal = Decimal("10000")` here per D-09.
- `src/pipeline/runner.py` — Integration point. Insert new step between line ~90 (quota) and `await self._persist(...)`. Pass the existing `regime: MarketRegime` and `h1_candles` into the new step. The risk step happens before candidate ORM rows are flushed, so no database `candidate_id` exists yet; risk logs must use signal fields (`strategy`, `direction`, `entry_price`, optionally an in-memory `signal_ref=id(sig)`) rather than a DB candidate id.
- `src/backtesting/regime_detector.py` — Produces `MarketRegime` with `atr_value`, `atr_pctile`, `regime`, `adx_value`. Risk sizer reuses these (D-06).
- `src/models/trade.py` — `TradeORM` (read-only consumer): `status`, `pnl`, `pnl_pct`, `close_reason`, `closed_at`, `direction`. Phase 6 queries this; Phase 7 writes it.
- `src/models/signal.py` — `ApprovedSignalORM.risk_check_passed: bool` already exists; gates set this. `CandidateSignalORM.status` already supports `REJECTED`.
- `src/models/regime.py` — `MarketRegimeORM` for joining historical regime context if needed.
- `src/models/signal_data.py` — `CandidateSignal`, `MarketRegime`, `MarketRegimeType` Pydantic DTOs. Risk DTOs (`RiskDecision`, `PositionSizing`, `CircuitBreakerAlert`) follow the same Pydantic v2 pattern.
- `src/database.py` — `AsyncSessionLocal` for `TradeORM` queries. Risk gate reads must be read-only and must not reuse or mutate the `_persist()` transaction in `PipelineRunner`; either `RiskGateRunner` opens its own short-lived read-only sessions or the caller passes an explicitly read-only session before `_persist()` begins.
- `src/monitoring/health.py` lines 27–28, 68–71 — `circuit_breaker`, `open_positions`, `daily_pnl_pct`, `signals_today` placeholders. Phase 6 wires the first three to real values; `signals_today` stays as quota-counted.

### Adjacent Phase Decisions That Bind Phase 6
- `.planning/phases/04-signal-pipeline/04-CONTEXT.md` D-04 — In-memory pipeline + single transaction at persist. Risk step must respect this: do all reads in-memory before `_persist` opens its transaction.
- `.planning/phases/04-signal-pipeline/04-CONTEXT.md` D-05 — Provider/execution split. No broker calls in `src/risk/`.
- `.planning/phases/05-backtesting-validation/05-CONTEXT.md` Locked Architecture Constraints — Risk params are NOT in the optimizer (already true; just don't break this).

### Reference (do NOT copy old broker assumptions)
- `AGENTS.md` Realignment Override (top of file) — Provider/execution split is normative; ignore older broker references in §13+.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/config.py:56–65` — Every risk threshold needed by Phase 6 already exists as a typed env var. Only addition: `theoretical_equity_usd` (D-09).
- `src/models/signal.py:ApprovedSignalORM.risk_check_passed` — bool field already in the schema, default True. Risk gates flip this to False on rejection.
- `src/models/trade.py:TradeORM` — Has `status`, `pnl`, `pnl_pct`, `close_reason`, `closed_at`, `direction`. All read-side fields needed by gates exist; no migration required for v1.
- `src/backtesting/regime_detector.py:RegimeDetector.detect()` — Already computes ATR(14) on H1 and returns `MarketRegime(atr_value, atr_pctile, regime, ...)`. Sizer reuses these directly (no recomputation).
- `src/models/signal_data.py` — Pydantic v2 DTO pattern; risk DTOs mirror `CandidateSignal` style (immutable, typed).
- `redis_url: str = "redis://redis:6379/0"` already in `Settings` and the Redis container is in `docker-compose.yml`. Phase 6 is the first runtime consumer beyond `monitoring/health.py`.
- `AsyncSessionLocal` for DB reads (`TradeORM` queries on UTC-day boundary).

### Established Patterns
- Async everywhere (`async def` on every gate evaluator and breaker method).
- Pure functions for stateless logic — same pattern as `dedup_signals`, `filter_conflicts`, `apply_quota`. The position sizer must be pure (D-08); breaker methods are stateful via Redis only.
- structlog at module level (`log = structlog.get_logger(__name__)`); structured key=value events.
- DTO/ORM separation strictly enforced (`signal_data.py` Pydantic vs `signal.py` ORM). Risk follows: `src/risk/events.py` for Pydantic DTOs, no ORM mutations from Phase 6.
- Tests under `tests/test_risk/` mirroring `tests/test_pipeline/` layout. Mock `AsyncSession`/`Redis` at the session client; reserve one integration test for live Redis.
- Pydantic v2 (`Field`, `model_config`); SQLAlchemy 2.0 async (`Mapped`, `mapped_column`).

### Integration Points
- New `src/risk/` package: `runner.py` (public `RiskGateRunner.evaluate`), `gates.py` (the 3 gates), `sizer.py` (pure `calculate_position_size`), `breaker.py` (`BreakerManager` against Redis), `events.py` (`RiskDecision`, `PositionSizing`, `CircuitBreakerAlert`).
- `src/pipeline/runner.py`: insert call between `apply_quota(...)` and `await self._persist(...)`; iterate quota survivors, call `RiskGateRunner.evaluate` for each, mark failed candidates as `REJECTED` in `status_map`, and keep passed candidates in `approved_ranked`. Since this is pre-persistence, do not expect candidate DB IDs in decisions or logs.
- `src/main.py` does NOT need changes — risk runs inline via the existing scheduled pipeline job.
- `src/monitoring/health.py`: Phase 6 wires `circuit_breaker` (from `BreakerManager.is_tripped()`), `open_positions` (count of `TradeORM` rows where `status='OPEN'`), and `daily_pnl_pct` (D-14 query). `signals_today` stays as today's `ApprovedSignalORM` count.
- Phase 7 (SIG-02) hooks into `BreakerManager.record_stop()` / `record_win()` when it implements the theoretical-trade lifecycle.
- Phase 7 (NOTIF-03) registers a Telegram callback against the `CircuitBreakerAlert` hook surface (D-13).

### Test Strategy
- Unit tests for each gate function (populated TradeORM, empty TradeORM, threshold edge cases, UTC day boundary).
- Pure-function tests for `calculate_position_size` covering: baseline, high-vol (×0.7), low-vol (×1.3), hard-cap clamp (e.g., low-vol × baseline × 1.3 above 2% caps to 2%), concentration halving applied after cap.
- `BreakerManager` tests against `fakeredis`: counter increment, trip on Nth stop, cooldown TTL, reset on win, reset on cooldown expiry including `consecutive_stops` reset to 0.
- One integration test (real Redis from compose) for the breaker happy path.
- Pipeline integration test: full pipeline run with the risk step injected, asserting `ApprovedSignalORM.risk_check_passed` is False for rejected candidates and that the corresponding `CandidateSignalORM.status = 'REJECTED'`.

</code_context>

<specifics>
## Specific Ideas

- ATR(14) on H1 — explicit user direction, matches strategies and future trailing-stop work.
- Redis namespace prefix `risk:cb:` — keeps risk keys discoverable and separable from any future cache keys.
- AGENTS.md §12.2 sizing pseudocode is the canonical reference for the sizer. Implement it line-for-line, not paraphrased.
- Circuit breaker reset rule per AGENTS.md §12.3 (first winning trade OR cooldown expiry).
- Use `fakeredis` for unit tests so the test suite stays Docker-free (mirrors how strategy/pipeline tests stay DB-free where possible).

</specifics>

<deferred>
## Deferred Ideas

- Telegram client (`src/monitoring/telegram_bot.py`) — Phase 7 (NOTIF-01..04). Phase 6 produces the alert event; Phase 7 delivers it.
- Theoretical-trade lifecycle (open → TP1 → trailing → close) populating `TradeORM` — Phase 7 (SIG-01, SIG-02, SIG-03).
- Auto-mode risk handling (real broker fills, real positions) — Phase 8/9.
- Daily-loss-limit tripping the circuit breaker (AGENTS.md §12.1 Gate 1 hint) — possible v2; not in ROADMAP success criterion (D-15).
- Persisting `rejection_reason` on `candidate_signals` and `size_lots` / `risk_pct` on `approved_signals` — Phase 7 if structured DB audit is needed there; logs cover v1 needs (D-05, D-08).
- Dynamic equity (apply realized daily P&L to baseline) — currently constant `theoretical_equity_usd` (D-09); upgrade when Phase 7 P&L tracking is robust.
- RISK-03 alternate semantics (block instead of reduce) — explicitly rejected per D-04 and AGENTS.md §12.1.
- Auto-mode "consecutive stops" against real broker fills — same breaker contract should hold; revisit only if broker fill semantics differ.

</deferred>

---

*Phase: 06-risk-management*
*Context gathered: 2026-04-26*
