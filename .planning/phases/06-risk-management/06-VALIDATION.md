---
phase: 6
slug: risk-management
status: active
nyquist_compliant: true
wave_0_complete: true
created: 2026-04-26
revised: 2026-04-27
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x (existing) |
| **Config file** | pyproject.toml + tests/conftest.py |
| **Quick run command** | `pytest tests/test_risk/ -x -q` |
| **Full suite command** | `pytest -x` |
| **Estimated runtime** | ~30 seconds (test_risk) / full suite TBD |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_risk/ -x -q`
- **After every plan wave:** Run `pytest -x`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 06-01-T1 | 06-01 | 1 | — | T-06-01-01 | fakeredis dep added without other dep changes | grep gate | `grep -c '"fakeredis>=2.20"' pyproject.toml` | pyproject.toml | ⬜ pending |
| 06-01-T2 | 06-01 | 1 | — | T-06-01-02 | src.risk importable; test_risk/ discoverable | pytest collect | `python -c "import src.risk" && pytest tests/test_risk/ --collect-only -q` | src/risk/__init__.py, tests/test_risk/__init__.py | ⬜ pending |
| 06-01-T3 | 06-01 | 1 | — | T-06-01-03, T-06-01-04 | fake_redis fixture function-scoped; no session/module scope | pytest collect + grep | `pytest tests/test_risk/ --collect-only -q && grep -E -c "scope=(\"session\"\|'session'\|\"module\"\|'module')" tests/test_risk/conftest.py` | tests/test_risk/conftest.py | ⬜ pending |
| 06-02-T1 | 06-02 | 2 | RISK-05 | T-06-02-01 | PositionSizing/RiskDecision/CircuitBreakerAlert importable; frozen=True | pytest | `pytest tests/test_risk/test_hooks.py -x -q` | src/risk/events.py, src/risk/hooks.py, tests/test_risk/test_hooks.py | ⬜ pending |
| 06-03-T1 | 06-03 | 2 | RISK-04 | — | theoretical_equity_usd defaults to Decimal('10000') | pytest | `pytest tests/test_config/test_settings.py -x -q` | src/config.py, tests/test_config/test_settings.py | ⬜ pending |
| 06-04-T1 | 06-04 | 3 | RISK-04, RISK-03 | T-06-04-01 | RED: sizer tests fail (src.risk.sizer missing) | pytest exit nonzero | `pytest tests/test_risk/test_sizer.py -x -q 2>&1; echo "exit:$?"` | tests/test_risk/test_sizer.py | ⬜ pending |
| 06-04-T2 | 06-04 | 3 | RISK-04, RISK-03 | T-06-04-01, T-06-04-02 | GREEN: all sizer tests pass; no I/O in calculate_position_size | pytest | `pytest tests/test_risk/test_sizer.py -x -q && grep -c "async def" src/risk/sizer.py` | src/risk/sizer.py | ⬜ pending |
| 06-05-T1 | 06-05 | 3 | RISK-01, RISK-02, RISK-03 | T-06-05-01 | RED: gate tests fail (src.risk.gates missing) | pytest exit nonzero | `pytest tests/test_risk/test_gates.py -x -q 2>&1; echo "exit:$?"` | tests/test_risk/test_gates.py, tests/test_risk/helpers.py | ⬜ pending |
| 06-05-T2 | 06-05 | 3 | RISK-01, RISK-02, RISK-03 | T-06-05-02, T-06-05-03 | GREEN: 9+ gate tests pass; AsyncSessionLocal absent; coalesce present | pytest + grep | `pytest tests/test_risk/test_gates.py -x -q && grep -c "AsyncSessionLocal" src/risk/gates.py` | src/risk/gates.py | ⬜ pending |
| 06-06-T1 | 06-06 | 3 | RISK-05 | T-06-06-01 | RED: breaker tests fail (src.risk.breaker missing) | pytest exit nonzero | `pytest tests/test_risk/test_breaker.py -x -q 2>&1; echo "exit:$?"` | tests/test_risk/test_breaker.py | ⬜ pending |
| 06-06-T2 | 06-06 | 3 | RISK-05 | T-06-06-01, T-06-06-02 | GREEN: 8+ breaker tests pass; fakeredis used | pytest | `pytest tests/test_risk/test_breaker.py -x -q` | src/risk/breaker.py | ⬜ pending |
| 06-07-T1 | 06-07 | 4 | RISK-01—RISK-05 | T-06-07-01 | RED: runner tests fail (src.risk.runner missing) | pytest exit nonzero | `pytest tests/test_risk/test_runner.py -x -q 2>&1; echo "exit:$?"` | tests/test_risk/test_runner.py | ⬜ pending |
| 06-07-T2 | 06-07 | 4 | RISK-01—RISK-05 | T-06-07-01, T-06-07-02 | GREEN: runner tests pass; RiskGateRunner importable from src.risk | pytest + python | `pytest tests/test_risk/test_runner.py -x -q && python -c "from src.risk import RiskGateRunner; print('ok')"` | src/risk/runner.py, src/risk/__init__.py | ⬜ pending |
| 06-08-T1 | 06-08 | 5 | RISK-01—RISK-05 | T-06-08-01, T-06-08-02 | Step 5.5 wired; RiskGateRunner import present; risk_session separate | grep + pytest | `grep -c "Step 5.5" src/pipeline/runner.py && grep -c "from src.risk import RiskGateRunner" src/pipeline/runner.py && pytest -x -q 2>&1 \| tail -3` | src/pipeline/runner.py, tests/test_pipeline/test_runner.py | ⬜ pending |
| 06-08-T2 | 06-08 | 5 | RISK-01—RISK-05 | T-06-08-05 | 2 integration tests pass: rejection no ApprovedORM, acceptance creates ApprovedORM | pytest | `pytest tests/test_pipeline/test_runner_risk_step.py -x -q` | tests/test_pipeline/test_runner_risk_step.py | ⬜ pending |
| 06-09-T1 | 06-09 | 6 | RISK-01, RISK-02, RISK-05 | T-06-09-01, T-06-09-02 | Placeholders replaced; try/except degrades gracefully | grep | `grep -c "circuit_breaker.*False" src/monitoring/health.py && grep -c "BreakerManager" src/monitoring/health.py` | src/monitoring/health.py | ⬜ pending |
| 06-09-T2 | 06-09 | 6 | RISK-01, RISK-02, RISK-05 | T-06-09-02, T-06-09-03 | 3 health risk tests pass: tripped breaker, live DB values, degradation | pytest | `pytest tests/test_monitoring/test_health_risk.py -x -q` | tests/test_monitoring/__init__.py, tests/test_monitoring/test_health_risk.py | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `tests/test_risk/__init__.py` — package marker (Plan 01, Task 2)
- [x] `tests/test_risk/conftest.py` — shared fixtures (fakeredis, mocked AsyncSession, _reset_alert_hooks) (Plan 01, Task 3)
- [x] `tests/test_risk/helpers.py` — _mock_session helper for safe import by test files (Plan 05, Task 1)
- [x] `tests/test_risk/test_gates.py` — RISK-01, RISK-02, RISK-03 gate stubs (Plan 05, Task 1 — RED)
- [x] `tests/test_risk/test_sizer.py` — RISK-04 sizing stubs (Plan 04, Task 1 — RED)
- [x] `tests/test_risk/test_breaker.py` — RISK-05 breaker stubs (Plan 06, Task 1 — RED)
- [x] `tests/test_risk/test_runner.py` — RiskGateRunner integration stubs (Plan 07, Task 1 — RED)
- [x] `tests/test_pipeline/test_runner_risk_step.py` — pipeline integration stub (Plan 08, Task 2)
- [x] `pyproject.toml` — `fakeredis>=2.20` added (Plan 01, Task 1)

*Wave 0 is the set of test scaffolds that must exist before implementation begins; they start RED and go GREEN as implementation plans execute.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| External notification channel circuit-breaker delivery end-to-end | RISK-05 (delivery side, deferred to Phase 7 / NOTIF-03) | Phase 6 emits the alert event only; actual External notification channel bot is Phase 7 | Phase 6 verifies alert event published via in-process hook (`BreakerAlertHook`) — automated. Real External notification channel delivery verified in Phase 7. |

*All other phase behaviors have automated verification per RESEARCH §Validation Architecture.*

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (test_gates.py RED, test_sizer.py RED, test_breaker.py RED, test_runner.py RED land in Wave 3–4; helpers.py provides _mock_session without cross-package import)
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** signed off (revision 2026-04-27 — Plans 01–09 mapped; Plan 09 added for health.py wiring; helpers.py added to replace brittle cross-package conftest import)
