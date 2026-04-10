---
phase: 04
slug: signal-pipeline
status: verified
threats_open: 0
asvs_level: 1
created: 2026-04-10
---

# Phase 04 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| StrategyRunner → Pipeline | list[CandidateSignal] passed in-memory; values come from internal strategy logic, not external input | Internal Pydantic objects — no secrets |
| Candle DB → RegimeDetector | Candle ORM objects from PostgreSQL; provider-sourced prices already normalized | OHLC floats — not sensitive |
| PipelineRunner → DB | Pydantic CandidateSignal objects converted to ORM rows; all values from internal strategy output | Internal trading data |
| apply_quota → approved_signals | COUNT query reads existing rows; no user-supplied WHERE clauses | Row count integer only |
| APScheduler → StrategyRunner/PipelineRunner | Scheduled job triggers internal pipeline; no external input | None — trigger only |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-04-01 | Tampering | `dedup_signals()` — input mutation | mitigate | New lists built; no attribute set on input CandidateSignal objects (`dedup.py:45–77`) | **closed** |
| T-04-02 | DoS | `RegimeDetector._calculate_adx` — ZeroDivisionError | mitigate | `smoothed_tr==0` guard (`regime_detector.py:167–169`) + `di_sum==0` guard (`regime_detector.py:174–178`) | **closed** |
| T-04-03 | Info Disclosure | structlog price/signal data in logs | accept | Logs are internal-only; no external exposure in v1 | **closed** |
| T-04-04 | Elevation of Privilege | Conflict filter tie-break determinism | mitigate | `>=` comparison ensures BUY wins tie deterministically; `rejected_count` always logged (`conflict_filter.py:50,59–64`) | **closed** |
| T-04-05 | Tampering | `_persist()` — partial DB write | mitigate | `async with session.begin()` wraps all 3 write operations atomically (`runner.py:143–144`) | **closed** |
| T-04-06 | DoS | `rank_signals` asyncio.gather WFE fetch | mitigate | Each WFE fetch uses `.limit(1)`; `asyncio.gather` over unique strategies only (`ranker.py:54,100`) | **closed** |
| T-04-07 | Repudiation | Pipeline audit trail completeness | mitigate | Every candidate persisted with final status (DEDUPED/REJECTED/APPROVED) via `status_map` (`runner.py:94–102,156–170`) | **closed** |
| T-04-08 | Info Disclosure | `params_snapshot` in CandidateSignalORM | accept | Strategy params are internal config values, not secrets; JSONB storage is the designed audit mechanism | **closed** |
| T-04-09 | Elevation of Privilege | Quota bypass via direct list injection | mitigate | `_count_today_approvals()` COUNT query executed before any approvals (`quota.py:28–36,59–63`) | **closed** |
| T-04-10 | DoS | `run_pipeline` scheduler pile-up | mitigate | `max_instances=1` on APScheduler job (`jobs.py:178–185`) | **closed** |
| T-04-11 | Repudiation | Silent pipeline job failure | mitigate | `try/except Exception` with `log.error("jobs.pipeline.failed", error=str(exc))` (`jobs.py:94–126`) | **closed** |
| T-04-12 | Tampering | H1 candle fetch — no user-controllable WHERE | accept | Candles fetched from internal PostgreSQL only; no user input in query | **closed** |
| T-04-13 | DoS | Unbounded H1 candle fetch | mitigate | `.limit(200)` cap enforced (`jobs.py:113`) | **closed** |

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-04-01 | T-04-03 | Log entries contain price levels and signal metadata. Acceptable: logs are internal-only, no external exposure in v1. | gsd-security-auditor | 2026-04-10 |
| AR-04-02 | T-04-08 | `params_snapshot` stored as JSONB in CandidateSignalORM. Acceptable: strategy params are internal config values, not credentials or secrets. | gsd-security-auditor | 2026-04-10 |
| AR-04-03 | T-04-12 | H1 candle fetch WHERE clause uses only internal constants (instrument="XAUUSD", timeframe="H1"). No user-controllable input path exists. | gsd-security-auditor | 2026-04-10 |

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-04-10 | 13 | 13 | 0 | gsd-security-auditor |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-04-10
