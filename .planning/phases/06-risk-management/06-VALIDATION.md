---
phase: 6
slug: risk-management
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-26
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

> Filled in by planner — each task in PLAN.md must list a `<automated>` block or a Wave 0 dependency. See 06-RESEARCH.md `## Validation Architecture` for the 22 falsifiable assertions, one+ per RISK-01..RISK-05.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 06-XX-XX | TBD | TBD | RISK-XX | — | TBD | TBD | TBD | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_risk/__init__.py` — package marker
- [ ] `tests/test_risk/conftest.py` — shared fixtures (fakeredis, mocked AsyncSession, populated/empty TradeORM)
- [ ] `tests/test_risk/test_gates.py` — RISK-01, RISK-02, RISK-03 gate stubs
- [ ] `tests/test_risk/test_sizer.py` — RISK-04 sizing stubs (baseline, ×0.7 high-vol, ×1.3 low-vol, hard-cap clamp, concentration halving)
- [ ] `tests/test_risk/test_breaker.py` — RISK-05 breaker stubs (counter, trip on Nth stop, cooldown TTL, reset on win, reset on cooldown expiry)
- [ ] `tests/test_risk/test_runner.py` — RiskGateRunner integration stubs (short-circuit when tripped, decision payload shape)
- [ ] `tests/test_pipeline/test_runner_risk_step.py` — pipeline integration stub (risk step inserted between quota and persist)
- [ ] `pyproject.toml` add `fakeredis>=2.20` to dev dependencies (only missing dep per RESEARCH §2)

*See 06-RESEARCH.md §3 (Standard Stack) for confirmed deps already installed: redis>=5.0, sqlalchemy>=2.0 async, pydantic>=2, structlog.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Telegram circuit-breaker delivery end-to-end | RISK-05 (delivery side, deferred to Phase 7 / NOTIF-03) | Phase 6 emits the alert event only; actual Telegram bot is Phase 7 | Phase 6 verifies alert event published via in-process hook (`BreakerAlertHook`) — automated. Real Telegram delivery verified in Phase 7. |

*All other phase behaviors have automated verification per RESEARCH §Validation Architecture.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
