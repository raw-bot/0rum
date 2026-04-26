# Phase 6: Risk Management - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-26
**Phase:** 06-risk-management
**Areas discussed:** Theoretical-position data source, Pipeline placement + rejection auditing, ATR sizing semantics (RISK-04), Circuit breaker state + alert delivery (RISK-05)

---

## Theoretical-position data source

| Option | Description | Selected |
|--------|-------------|----------|
| Stub-now | Phase 6 reads from a source that returns zeros until Phase 7 fills it; gates degrade gracefully | ✓ |
| Build minimal ledger | Phase 6 writes TradeORM rows on signal approval and resolves them off candle highs/lows so RISK-01/02/03/05 are exercisable end-to-end now | |

**User's choice:** Stub-now (read-only `TradeORM`).
**Notes:** "Phase 6 doit exposer les fonctions/gates et tests sans inventer tout Phase 7." Use existing `TradeORM` if rows are present; treat empty table as the normal state until Phase 7 (SIG-02) ships. Phase 6 NEVER mutates `TradeORM`. Tests must cover both populated and empty states. Daily P&L is `SUM(pnl_pct)` over UTC-day closed trades; open trades do not contribute (no mark-to-market). Equity baseline = new env var `theoretical_equity_usd` (default 10000); dynamic equity is deferred. → CONTEXT.md D-01, D-09, D-14.

---

## Pipeline placement + rejection auditing

| Option | Description | Selected |
|--------|-------------|----------|
| New step between quota and persist | RISK-01/02/03 run as step 5.5; quota stays as MAX_SIGNALS_PER_DAY | ✓ |
| Replace quota | RISK-02 absorbs MAX_SIGNALS_PER_DAY; one combined "limits" gate | |
| Risk before quota | Run risk gates earlier so quota doesn't waste budget on rejected signals | |
| Rejection reason: structlog only | No DB schema change; structured `risk.gate.rejected` events carry `reason` | ✓ |
| Rejection reason: new column on candidate_signals | Migrate `candidate_signals.rejection_reason TEXT` for queryable audit | |

**User's choice:** New step 5.5 (between quota and persist) + structlog-only rejection reasons.
**Notes:** "logique risk dans nouveau src/risk/. pipeline/runner.py ne doit appeler qu'une interface claire de risk gate plus tard, sans absorber la logique." `RiskGateRunner.evaluate(...)` is the only thing `pipeline/runner.py` imports from `src.risk`. `MAX_SIGNALS_PER_DAY` quota and `max_positions` (RISK-02) are semantically distinct (signals/day vs concurrent open positions) — both stay. Rejection reason persisted only in structlog for v1; column extension deferred. → CONTEXT.md D-02, D-03, D-05.

---

## ATR sizing semantics (RISK-04)

| Option | Description | Selected |
|--------|-------------|----------|
| ATR(14) on H1, reuse RegimeDetector output | Sizer consumes existing `MarketRegime.atr_value` / `atr_pctile` | ✓ |
| ATR(14) on M15, recompute in sizer | Per-signal ATR recomputation on M15 timeframe | |
| Sizer returns `risk_pct` + `size_lots` (PositionSizing DTO) | Pure function; lot conversion when equity is known | ✓ |
| Sizer returns just a multiplier | Caller multiplies risk_per_trade externally | |
| Symmetric high/low-vol scaling (×0.7 / ×1.3) per AGENTS §12.2 | Both branches kept even though ROADMAP only mentions high-vol reduction | ✓ |
| High-vol-only scaling (strict ROADMAP reading) | Skip the +30% low-vol branch | |
| Hard cap before concentration halving | 2% cap is the maximum non-concentrated risk; concentration further halves | ✓ |
| Hard cap after concentration halving | Halving could sit above 2% before the cap clamps it | |

**User's choice:** ATR(14) H1 (consistent with strategies), output = lot or multiplier per available info, symmetric scaling, cap before concentration halving.
**Notes:** "utiliser ATR(14), probablement H1, cohérent avec les stratégies et le trailing futur. output attendu: suggested size lots ou size multiplier selon infos disponibles." `RegimeDetector` already produces ATR(14) on H1 — reuse, no recomputation. Sizer is a pure function returning `PositionSizing` (`risk_pct`, `risk_amount_usd`, `size_lots`, `vol_factor`, `concentration_reduced`). Order: vol-factor → cap → concentration (so the 2% cap is the maximum risk for a non-concentrated trade). Symmetric +30% in low-vol IS in scope per AGENTS.md §12.2; verifier should not flag as scope creep. RISK-03 reduces 50% (D-04), does not block. → CONTEXT.md D-04, D-06, D-07, D-08, D-16.

---

## Circuit breaker state + alert delivery (RISK-05)

| Option | Description | Selected |
|--------|-------------|----------|
| Redis state under `risk:cb:` prefix | `consecutive_stops`, `tripped_at`, `cooldown_until` keys; TTL on cooldown keys | ✓ |
| Postgres state via new `circuit_breaker_state` table | Survives Redis flushes; queryable history | |
| Counter reset on first winning trade OR cooldown expiry | Per AGENTS.md §12.3 | ✓ |
| Counter reset only on cooldown expiry | Stricter — winning trade alone doesn't reset | |
| Telegram in Phase 6 (build minimal client) | `src/monitoring/telegram_bot.py` shipped in this phase | |
| Phase 6 emits structured event; Phase 7 wires Telegram | Hook surface defined now, delivery deferred | ✓ |
| RISK-01 trips the breaker (per AGENTS §12.1) | Daily-loss limit triggers cooldown | |
| RISK-01 only blocks (per ROADMAP) | Strict ROADMAP reading; breaker only on RISK-05's 8 stops | ✓ |

**User's choice:** Redis state, hooks-based alert (Phase 7 wires Telegram), reset on win OR cooldown expiry, signal-mode theoretical stops only.
**Notes:** "Redis est prévu pour l'état mutable du breaker. Comme Telegram n'est pas encore implémenté, Phase 6 peut logguer/retourner un event d'alerte, et Phase 7 branchera Telegram." `BreakerManager` exposes `is_tripped`, `record_stop`, `record_win`, `reset_if_expired`. `record_stop` returns a `CircuitBreakerAlert` Pydantic event ONLY when this stop is the trip; an in-process hook surface lets Phase 7 register Telegram delivery against it without changes to risk code. While tripped, the gate evaluator short-circuits all candidates to REJECTED with reason `circuit_breaker_active`. RISK-01 does NOT trip the breaker in Phase 6 (deferred — easy to add later). "Consecutive stops" = theoretical stops in signal mode (`TradeORM.close_reason='sl_hit' AND status='CLOSED'`); Phase 7 calls `record_stop`/`record_win` from the lifecycle handlers. → CONTEXT.md D-10, D-11, D-12, D-13, D-13b, D-15.

---

## Claude's Discretion

- Internal layout of `src/risk/` (single module vs split into `gates.py`, `sizer.py`, `breaker.py`, `events.py`, `runner.py`).
- Whether `RiskGateRunner.evaluate` lives at `src/risk/runner.py` or `src/pipeline/risk.py`.
- `fakeredis` vs live Redis fixture for tests.
- Exact `RiskDecision` schema fields and naming.
- structlog event naming convention (`risk.gate.daily_loss_limit.rejected` vs flat with `gate=`).
- `theoretical_equity_usd` typed as `Decimal` vs `float`.
- Test fixture strategy for empty-TradeORM cases (mocked `AsyncSession.execute` vs populated test DB).

## Deferred Ideas

- Telegram client (`src/monitoring/telegram_bot.py`) — Phase 7 (NOTIF-01..04).
- Theoretical-trade lifecycle (open → TP1 → trailing → close) populating `TradeORM` — Phase 7 (SIG-01..03).
- Auto-mode risk handling (real broker fills, real positions) — Phase 8/9.
- Daily-loss-limit tripping the circuit breaker (AGENTS.md §12.1 Gate 1 hint) — possible v2.
- Persisting `rejection_reason` on `candidate_signals` and `size_lots`/`risk_pct` on `approved_signals` — Phase 7 if structured DB audit is needed.
- Dynamic equity (apply realized daily P&L to baseline) — upgrade when Phase 7 P&L tracking matures.
- RISK-03 alternate semantics (block instead of reduce) — explicitly rejected per D-04 and AGENTS.md §12.1.
- Auto-mode "consecutive stops" against real broker fills — same breaker contract should hold.
