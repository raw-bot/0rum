---
phase: 5
slug: backtesting-validation
status: complete
nyquist_compliant: true
wave_0_complete: true
created: 2026-04-22
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (both in pyproject.toml) |
| **Config file** | `pyproject.toml` (`asyncio_mode = "auto"`) |
| **Quick run command** | `pytest tests/test_backtesting/ -x -q` |
| **Full suite command** | `./.venv/bin/python -m pytest -q` |
| **Estimated runtime** | ~10 seconds (unit tests with mocked DB) |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_backtesting/ -x -q`
- **After every plan wave:** Run `pytest -x -q` (full 143-test suite must stay green)
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** ~15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 05-01-01 | 01 | 1 | OPTIM-01 | — | LHS values clamped to PARAM_RANGES | unit | `pytest tests/test_backtesting/test_optimizer.py::test_lhs_produces_100_combos -x` | ✅ | ✅ green |
| 05-01-02 | 01 | 1 | OPTIM-01 | — | LHS values within declared bounds | unit | `pytest tests/test_backtesting/test_optimizer.py::test_lhs_values_in_range -x` | ✅ | ✅ green |
| 05-01-03 | 01 | 1 | OPTIM-02 | — | WFE < 0.50 blocks param activation | unit | `pytest tests/test_backtesting/test_walk_forward.py::test_wfe_gate_blocks_low_wfe -x` | ✅ | ✅ green |
| 05-01-04 | 01 | 1 | OPTIM-02 | — | Strategy retains previous params on WFE failure | unit | `pytest tests/test_backtesting/test_optimizer.py::test_retains_previous_on_failure -x` | ✅ | ✅ green |
| 05-01-05 | 01 | 1 | OPTIM-03 | — | 2/3 OOS profitable → gate passes | unit | `pytest tests/test_backtesting/test_walk_forward.py::test_multiwindow_2_of_3_passes -x` | ✅ | ✅ green |
| 05-01-06 | 01 | 1 | OPTIM-03 | — | 1/3 OOS profitable → gate fails | unit | `pytest tests/test_backtesting/test_walk_forward.py::test_multiwindow_1_of_3_fails -x` | ✅ | ✅ green |
| 05-01-07 | 01 | 1 | — | — | Insufficient data → optimizer skips, no DB write | unit | `pytest tests/test_backtesting/test_optimizer.py::test_data_guard_skips_activation -x` | ✅ | ✅ green |
| 05-02-01 | 02 | 1 | OPTIM-05 | — | P95 drawdown ≤ 2× historical passes gate | unit | `pytest tests/test_backtesting/test_monte_carlo.py::test_p95_dd_gate -x` | ✅ | ✅ green |
| 05-02-02 | 02 | 1 | OPTIM-05 | — | P5 profit factor > 1.0 passes gate | unit | `pytest tests/test_backtesting/test_monte_carlo.py::test_p5_pf_gate -x` | ✅ | ✅ green |
| 05-02-03 | 02 | 1 | OPTIM-05 | — | Both gates must pass (not just one) | unit | `pytest tests/test_backtesting/test_monte_carlo.py::test_both_gates_required -x` | ✅ | ✅ green |
| 05-03-01 | 03 | 1 | OPTIM-04 | — | Optimizer job registered in scheduler | unit | `pytest tests/test_backtesting/test_scheduler_wiring.py::test_optimizer_job_registered -x` | ✅ | ✅ green |
| 05-03-02 | 03 | 1 | OPTIM-04 | — | Optimizer job has max_instances=1 | unit | `pytest tests/test_backtesting/test_scheduler_wiring.py::test_optimizer_job_max_instances -x` | ✅ | ✅ green |
| 05-04-01 | 04 | 2 | DATA | — | HistData CSV parses and resamples correctly | unit | `pytest tests/test_backtesting/test_historical_loader.py::test_resample_m1_to_m15_ohlcv -x` | ✅ | ✅ green |
| 05-04-02 | 04 | 2 | DATA | — | Bulk insert uses ON CONFLICT DO NOTHING | unit | `pytest tests/test_backtesting/test_historical_loader.py::test_bulk_insert_candles_is_idempotent_with_sqlite -x` | ✅ | ✅ green |
| 05-05-01 | 05 | 2 | ALL OPTIM | — | Full optimizer run on synthetic data produces is_active=True row | integration | `pytest tests/test_backtesting/test_optimizer_integration.py -x` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `tests/test_backtesting/__init__.py` — package init
- [x] `tests/test_backtesting/test_optimizer.py` — LHS, WFE gate, data guard, retain-previous
- [x] `tests/test_backtesting/test_walk_forward.py` — window construction, multi-window gate, WFE calc
- [x] `tests/test_backtesting/test_monte_carlo.py` — P95/P5 gate tests
- [x] `tests/test_backtesting/test_scheduler_wiring.py` — optimizer job registration
- [x] `tests/test_backtesting/test_historical_loader.py` — CSV parse, resample, bulk insert (Wave 2 / Chemin B only)
- [x] `tests/test_backtesting/test_optimizer_integration.py` — end-to-end synthetic run
- [x] `src/backtesting/__init__.py` — package init

**Fixtures pattern:** Tests MUST use synthetic Candle fixtures (not live DB), following the pattern in `tests/conftest.py` (env vars set before `src.*` import). All DB interactions mocked via `AsyncMock` or SQLite.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| HistData.com data download covers 24m+ of XAUUSD M1 | DATA (offline bootstrap) | Requires downloaded vendor archives | Run `./.venv/bin/python scripts/histdata_phase5_loader.py qa` — checks archive row counts and resampled M15/H1/H4/D1 counts |
| WFE > 50% achieved on real 6m/2m XAUUSD window | OPTIM-02 | Requires real historical data (not synthetic) | Verified: `liquidity_sweep` active with WFE `1.8478` |
| Full test suite stays green after Phase 5 | All | Regression check | Verified: `212 passed in 9.21s` |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 15s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** complete — 2026-04-26
