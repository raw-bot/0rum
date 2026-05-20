---
phase: 7
slug: signal-mode-monitoring
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-04-28
---

# Phase 7 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio 0.23+ |
| **Config file** | `[tool.pytest.ini_options]` in `pyproject.toml`; `asyncio_mode = "auto"` |
| **Quick run command** | `pytest tests/test_execution/ tests/test_monitoring/ -x` |
| **Full suite command** | `pytest` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_execution/ tests/test_monitoring/ -x --tb=short`
- **After every plan wave:** Run `pytest`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 07-01-01 | 01 | 1 | SIG-01 | T-07-01 / — | External notification channel token never logged in execution.send_failed event | unit | `pytest tests/test_execution/test_signal_sender.py -x` | ❌ W0 | ⬜ pending |
| 07-01-02 | 01 | 1 | NOTIF-01 | — | N/A | unit | `pytest tests/test_execution/test_executor.py::test_status_sent_on_success -x` | ❌ W0 | ⬜ pending |
| 07-01-03 | 01 | 1 | NOTIF-01 | — | keep PENDING + log on failure; no silent discard | unit | `pytest tests/test_execution/test_executor.py::test_status_pending_on_failure -x` | ❌ W0 | ⬜ pending |
| 07-02-01 | 02 | 1 | SIG-02 | — | N/A | unit | `pytest tests/test_monitoring/test_monitor_trades.py::test_tp1_hit -x` | ❌ W0 | ⬜ pending |
| 07-02-02 | 02 | 1 | SIG-02 | — | N/A | unit | `pytest tests/test_monitoring/test_monitor_trades.py::test_trail_ratchet -x` | ❌ W0 | ⬜ pending |
| 07-02-03 | 02 | 1 | SIG-02 | — | SL wins pre-TP1_HIT (conservative worst-case) | unit | `pytest tests/test_monitoring/test_monitor_trades.py::test_sl_wins_pre_tp1 -x` | ❌ W0 | ⬜ pending |
| 07-03-01 | 03 | 1 | SIG-03 | — | N/A | unit | `pytest tests/test_monitoring/test_strategy_stats.py -x` | ❌ W0 | ⬜ pending |
| 07-04-01 | 04 | 2 | NOTIF-02 | — | N/A | unit | `pytest tests/test_monitoring/test_notification_adapter.py::test_lifecycle_notifications -x` | ❌ W0 | ⬜ pending |
| 07-04-02 | 04 | 2 | NOTIF-03 | — | N/A | unit | `pytest tests/test_monitoring/test_notification_adapter.py::test_cb_alert -x` | ❌ W0 | ⬜ pending |
| 07-04-03 | 04 | 2 | NOTIF-04 | — | N/A | unit | `pytest tests/test_monitoring/test_notification_adapter.py::test_daily_summary -x` | ❌ W0 | ⬜ pending |
| 07-05-01 | 05 | 2 | SIG-02 | — | N/A | unit | `pytest tests/test_monitoring/test_dashboard.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_execution/__init__.py` — new test package for execution module
- [ ] `tests/test_execution/test_signal_sender.py` — stubs for SIG-01, NOTIF-01
- [ ] `tests/test_execution/test_executor.py` — stubs for NOTIF-01 (D-13/D-14/D-15)
- [ ] `tests/test_monitoring/test_monitor_trades.py` — stubs for SIG-02, NOTIF-02
- [ ] `tests/test_monitoring/test_strategy_stats.py` — stubs for SIG-03
- [ ] `tests/test_monitoring/test_notification_adapter.py` — stubs for NOTIF-02, NOTIF-03, NOTIF-04
- [ ] `tests/test_monitoring/test_dashboard.py` — stubs for /dashboard, /api/dashboard

*Note: `tests/test_monitoring/__init__.py` and `tests/test_monitoring/test_health_risk.py` already exist — add to existing package.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| External notification channel signal message delivered to chat | NOTIF-01 | Requires live External notification channel bot token + chat | Set valid token, run bot in signal mode, generate an approved signal, verify message received in External notification channel |
| Daily summary fires at 00:00 UTC | NOTIF-04 | Requires real-time clock + live External notification channel | Advance system clock or use APScheduler immediate trigger; verify message in External notification channel chat |
| Dashboard renders correctly at /dashboard | D-21 | Browser rendering requires visual inspection | Open http://localhost:8000/dashboard, verify sections load, verify auto-refresh updates timestamp |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
