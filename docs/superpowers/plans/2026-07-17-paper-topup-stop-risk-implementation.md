# Paper Topup And Stop-Risk Implementation Plan

> **SUPERSEDED IN PART — 2026-07-17:** tranche identity and topup remain;
> sizing, cap budgets and R were restored to the historical `2×ATR` basis at
> the operator's request. The notional leverage cap remains 3x.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote the proven thesis-budget topup policy into the paper engine while sizing and capping every tranche from its real stop distance.

**Architecture:** Keep `PaperBroker` as the single accounting authority, but give every position a distinct `position_id` while retaining a stable `strategy_id` for strategy ownership. `PaperEngine` computes the effective risk distance, runs a deterministic merit auction for explicit `topup` configurations, and manages protective exits per tranche while strategy exits close the whole strategy. The replay harness becomes a thin configuration wrapper over the production behavior so paper and research cannot drift again.

**Tech Stack:** Python 3.12, dataclasses, JSON/JSONL state, YAML configuration, unittest, existing replay harness and vanilla JavaScript dashboard.

---

## Guardrails and file map

- `orum/portfolio/paper_broker.py`: tranche identity, backward-compatible state loading, sizing and R accounting from `risk_distance`.
- `orum/portfolio/paper_engine.py`: stop-distance validation, explicit hold/topup policy, merit auction, tranche lifecycle and cap accounting.
- `scripts/replay_harness/arbiter.py`: compatibility adapter that configures the production engine; no copied `run_cycle`.
- `scripts/replay_harness/arbiter_report.py`: reports base strategies while using actual stop risk.
- `scripts/replay_harness/reprice.py`: pairs fills by position and reproduces corrected sizing.
- `orum/dashboard.py`, `orum/static/dashboard.js`: aggregate tranches by stable strategy and expose actual stop risk.
- `state/portfolio.yaml`, `config/portfolio.yaml`: activate `topup`, UT Bot first, zero minimum fraction; do not alter any risk percentage.
- `tests/test_paper_broker.py`, `tests/test_paper_engine.py`, `tests/test_arbiter.py`, `tests/test_replay_harness.py`, `tests/test_dashboard_terminal.py`: regression proof.
- `backtests/reports/chantier4_paper_topup_stop_risk.md`: fresh hold/topup evidence after corrected sizing.

The worktree already contains user changes in several of these files. Every edit must use a narrow patch, and every checkpoint must inspect `git diff -- <paths>`. Do not restart the worker, watcher, dashboard or producer. Do not stage pre-existing changes.

### Task 1: Give the broker a stable strategy identity and distinct tranche identity

**Files:**
- Modify: `orum/portfolio/paper_broker.py`
- Test: `tests/test_paper_broker.py`

- [ ] **Step 1: Write failing broker tests**

Add tests equivalent to:

```python
def test_open_sizes_from_real_stop_distance_and_keeps_atr_diagnostic():
    account = Account(balance_usd=10_000)
    fill = PaperBroker(fee_rt=0).open(
        account, strategy_id="ut", position_id="ut::t2", symbol="BTC/USDT",
        price=100, atr_risk=10, risk_distance=20, risk_pct=0.02,
        equity_for_sizing=10_000, stop_loss_price=80,
    )
    assert fill["strategy_id"] == "ut"
    assert fill["position_id"] == "ut::t2"
    assert fill["qty"] == 10
    assert account.positions["ut::t2"].atr_risk == 10
    assert account.positions["ut::t2"].risk_distance == 20

def test_account_loads_legacy_position_with_key_and_atr_fallback():
    account = Account.from_dict({"balance_usd": 10_000, "positions": {
        "ut": {"strategy_id": "ut", "symbol": "BTC/USDT", "side": "long",
               "qty": 1, "entry_px": 100, "notional_usd": 100,
               "risk_pct": .01, "atr_risk": 7}
    }}, starting_balance=10_000)
    assert account.positions["ut"].position_id == "ut"
    assert account.positions["ut"].risk_distance == 7
```

- [ ] **Step 2: Run the tests and observe RED**

Run: `uv run python -m unittest tests.test_paper_broker -v`

Expected: failures because `position_id` and `risk_distance` do not exist.

- [ ] **Step 3: Implement the minimal broker contract**

Add persisted fields and fallback loading:

```python
@dataclass
class Position:
    strategy_id: str
    symbol: str
    side: str
    qty: float
    entry_px: float
    notional_usd: float
    risk_pct: float
    atr_risk: float
    position_id: str = ""
    risk_distance: float = 0.0

    @property
    def stop_risk_usd(self) -> float:
        return self.qty * self.risk_distance
```

In `Account.from_dict`, construct a normalized payload per dictionary key:

```python
normalized = {key: value for key, value in raw.items() if key in position_fields}
normalized.setdefault("position_id", position_key)
normalized.setdefault("risk_distance", float(normalized.get("atr_risk", 0.0)))
normalized.setdefault("strategy_id", position_key.split("::t", 1)[0])
```

Extend `PaperBroker.open` with keyword-only `position_id: str | None = None` and `risk_distance: float | None = None`. Store under `position_key = position_id or strategy_id`, size with `effective_risk = risk_distance if risk_distance is not None else atr_risk`, and write both fields to the fill. Extend `close` with `position_id: str | None = None`, look up by that key, compute R with `pos.risk_distance`, and emit the stable `pos.strategy_id` plus `pos.position_id`.

- [ ] **Step 4: Run broker tests GREEN**

Run: `uv run python -m unittest tests.test_paper_broker -v`

Expected: all broker tests pass, including old single-position behavior.

### Task 2: Derive and validate the actual entry risk distance

**Files:**
- Modify: `orum/portfolio/paper_engine.py`
- Test: `tests/test_paper_engine.py`

- [ ] **Step 1: Add focused failing tests**

Cover all three cases with synthetic signals:

```python
def test_explicit_stop_sizes_from_entry_minus_stop(self):
    # LONG at 100, suggested stop 80, ATR basis 10 -> risk_distance must be 20.
    result, fills = self.run_engine(signal=long_signal(stop_loss_price=80), close=100)
    self.assertEqual(fills[0]["risk_distance"], 20)

def test_missing_stop_uses_atr_fallback(self):
    result, fills = self.run_engine(signal=long_signal(stop_loss_price=None), close=100)
    self.assertEqual(fills[0]["risk_distance"], fills[0]["atr_risk"])

def test_malformed_long_stop_is_rejected(self):
    result, fills = self.run_engine(signal=long_signal(stop_loss_price=101), close=100)
    self.assertEqual(result["intents"]["ut"], "invalid_stop")
    self.assertEqual(fills, [])
```

- [ ] **Step 2: Run RED**

Run: `uv run python -m unittest tests.test_paper_engine.PaperEngineTests.test_explicit_stop_sizes_from_entry_minus_stop tests.test_paper_engine.PaperEngineTests.test_missing_stop_uses_atr_fallback tests.test_paper_engine.PaperEngineTests.test_malformed_long_stop_is_rejected -v`

Expected: missing `risk_distance` and malformed stop currently opens.

- [ ] **Step 3: Add one pure helper and use it at entry**

Implement:

```python
def _entry_risk_distance(entry_price: float, stop_loss_price: float | None,
                         atr_risk: float) -> float | None:
    if stop_loss_price is None:
        return atr_risk if atr_risk > 0 else None
    distance = entry_price - float(stop_loss_price)
    return distance if distance > 0 else None
```

Freeze this value in the plan before allocation. A `None` result sets `invalid_stop`; otherwise pass it into `PaperBroker.open`. Replace cap sums `position.qty * position.atr_risk` with `position.stop_risk_usd`.

- [ ] **Step 4: Run the focused tests and full engine file GREEN**

Run: `uv run python -m unittest tests.test_paper_engine -v`

Expected: all paper engine tests pass.

### Task 3: Promote explicit hold/topup auction and tranche lifecycle

**Files:**
- Modify: `orum/portfolio/paper_engine.py`
- Test: `tests/test_paper_engine.py`

- [ ] **Step 1: Add failing policy and lifecycle tests**

Add tests that assert:

```python
topup = {"reentry_policy": "topup",
         "merit_order": ["btc_utbot_m15_h1", "btc_ak_macd_4h"],
         "min_topup_fraction": 0.0,
         "max_symbol_stop_risk_pct": 0.03}
```

- two simultaneous BTC LONG intents allocate UT Bot before AK regardless of config order;
- a later UT Bot LONG opens `btc_utbot_m15_h1::t2` only for remaining stop-risk budget;
- a protective stop closes only its `position_id`;
- a strategy EXIT closes every position whose `strategy_id` matches;
- explicit `hold` returns `reentry_hold` and opens no tranche;
- configurations without `reentry_policy` preserve the current one-position, all-or-nothing path.

- [ ] **Step 2: Run policy tests RED**

Run: `uv run python -m unittest tests.test_paper_engine -v`

Expected: new tranche and auction assertions fail.

- [ ] **Step 3: Parse policy without changing legacy defaults**

In `PaperEngine.__init__`:

```python
self._reentry_policy = self._config.get("reentry_policy")
if self._reentry_policy not in (None, "hold", "topup"):
    raise ValueError(f"unknown reentry_policy {self._reentry_policy!r}")
self._min_topup_fraction = float(self._config.get("min_topup_fraction", 0.0))
configured = {sc.id for sc in self._strategies}
merit = list(self._config.get("merit_order") or [])
unknown = set(merit) - configured
if unknown:
    raise ValueError(f"merit_order references unknown strategies: {sorted(unknown)}")
self._merit_rank = {sid: index for index, sid in enumerate(merit)}
```

- [ ] **Step 4: Add tranche helpers**

```python
TRANCHE_SEP = "::t"

@staticmethod
def _position_ids(account: Account, strategy_id: str) -> list[str]:
    return [pid for pid, position in account.positions.items()
            if position.strategy_id == strategy_id]

def _next_position_id(self, account: Account, strategy_id: str) -> str:
    if strategy_id not in account.positions:
        return strategy_id
    index = 2
    while f"{strategy_id}{TRANCHE_SEP}{index}" in account.positions:
        index += 1
    return f"{strategy_id}{TRANCHE_SEP}{index}"
```

- [ ] **Step 5: Implement the explicit auction only when configured**

Keep the existing path intact when `_reentry_policy is None`. For explicit policies, gather valid LONG requests, sort with `(merit rank, config rank)`, recompute open total and symbol stop-risk before every grant, and use:

```python
grant_usd = min(requested_usd, remaining_symbol_usd, remaining_total_usd)
grant_fraction = grant_usd / requested_usd if requested_usd > 0 else 0.0
if held and self._reentry_policy == "hold":
    intent = "reentry_hold"
elif grant_usd <= 0:
    intent = "thesis_already_funded"
elif grant_fraction < self._min_topup_fraction:
    intent = "topup_below_floor"
else:
    effective_risk_pct = grant_usd / equity_for_sizing
```

Open with the generated `position_id`. Iterate matching positions for protective exits. Close only the hit `position_id` for SL/TP, and all matching positions for a strategy EXIT.

- [ ] **Step 6: Run engine tests GREEN**

Run: `uv run python -m unittest tests.test_paper_engine -v`

Expected: all existing and new engine tests pass.

### Task 4: Remove harness/production drift and correct replay repricing

**Files:**
- Modify: `scripts/replay_harness/arbiter.py`
- Modify: `scripts/replay_harness/arbiter_report.py`
- Modify: `scripts/replay_harness/reprice.py`
- Test: `tests/test_arbiter.py`
- Test: `tests/test_replay_harness.py`

- [ ] **Step 1: Add failing parity and fill-pairing tests**

Assert the harness class inherits the production implementation without overriding `run_cycle`, arbiter fills retain stable `strategy_id`, `position_id` distinguishes tranches, and repricing pairs open/close records by `position_id`.

- [ ] **Step 2: Run RED**

Run: `uv run python -m unittest tests.test_arbiter tests.test_replay_harness -v`

Expected: copied engine behavior and strategy-id pairing violate the new contract.

- [ ] **Step 3: Turn the arbiter into a configuration adapter**

Replace the duplicated lifecycle with:

```python
def make_arbiter_engine_class():
    from orum.portfolio.paper_engine import PaperEngine

    class ThesisArbiterEngine(PaperEngine):
        def __init__(self, config, *, reentry_policy="hold", merit_order=None,
                     min_topup_fraction=0.0, **kwargs):
            configured = dict(config)
            configured.update({
                "reentry_policy": reentry_policy,
                "merit_order": list(merit_order or []),
                "min_topup_fraction": float(min_topup_fraction),
            })
            super().__init__(configured, **kwargs)

    return ThesisArbiterEngine
```

Retain `base_strategy_id` only for legacy ledgers.

- [ ] **Step 4: Correct reports and repricer**

Use `fill.get("position_id", fill["strategy_id"])` as the pairing key, keep `strategy_id` for attribution, propagate `risk_distance`, and size with `risk_pct * equity / risk_distance`. Risk distributions must use `qty * risk_distance`.

- [ ] **Step 5: Run harness tests GREEN**

Run: `uv run python -m unittest tests.test_arbiter tests.test_replay_harness -v`

Expected: all tests pass.

### Task 5: Aggregate tranches correctly on the dashboard

**Files:**
- Modify: `orum/dashboard.py`
- Modify: `orum/static/dashboard.js`
- Test: `tests/test_dashboard_terminal.py`

- [ ] **Step 1: Add failing dashboard tests**

Build state with `ut` and `ut::t2`, both carrying `strategy_id="ut"`, then assert the API returns one UT strategy row with `tranche_count == 2`, summed notional, summed unrealized PnL, summed `stop_risk_usd`, and per-tranche details. Assert closed trades pair by `position_id`, marker filtering uses stable `strategy_id`, and the UT label remains `LONG ONLY`.

- [ ] **Step 2: Run RED**

Run: `uv run python -m unittest tests.test_dashboard_terminal -v`

Expected: one-row-per-position logic and strategy-id fill pairing fail.

- [ ] **Step 3: Implement server aggregation and client display**

Normalize legacy positions with:

```python
strategy_id = position.get("strategy_id") or position_id.split("::t", 1)[0]
risk_distance = float(position.get("risk_distance", position.get("atr_risk", 0.0)))
stop_risk_usd = float(position.get("qty", 0.0)) * risk_distance
```

Group by `strategy_id`, return `tranches`, and pair fills with `position_id` fallback. Render the aggregate values and a compact tranche count/details section without changing unrelated dashboard layout.

- [ ] **Step 4: Run dashboard tests GREEN**

Run: `uv run python -m unittest tests.test_dashboard_terminal -v`

Expected: all dashboard terminal tests pass.

### Task 6: Activate topup in paper configuration without reducing risk

**Files:**
- Modify: `state/portfolio.yaml`
- Modify: `config/portfolio.yaml`
- Test: `tests/test_paper_engine.py`

- [ ] **Step 1: Add a configuration-loading assertion**

Assert the active state configuration contains exactly:

```yaml
reentry_policy: topup
merit_order:
  - btc_utbot_m15_h1
  - btc_ak_macd_4h
min_topup_fraction: 0.0
```

- [ ] **Step 2: Run RED**

Run: `uv run python -m unittest tests.test_paper_engine -v`

Expected: active configuration lacks the three keys.

- [ ] **Step 3: Add only the approved keys**

Add the same three keys to `state/portfolio.yaml` and the fallback `config/portfolio.yaml`. Do not change `risk_pct`, symbol/portfolio caps, strategy enablement, SL/TP parameters, or any live state file.

- [ ] **Step 4: Validate configuration without starting or restarting anything**

Run: `uv run python scripts/run_paper_portfolio.py --dry`

Expected: configuration and engine construction succeed; no order is sent and no daemon is restarted.

### Task 7: Re-run the corrected experiment and document the new evidence

**Files:**
- Create: `backtests/reports/chantier4_paper_topup_stop_risk.md`

- [ ] **Step 1: Run focused and full regression suites**

Run:

```bash
uv run python -m unittest tests.test_paper_broker tests.test_paper_engine tests.test_arbiter tests.test_replay_harness tests.test_dashboard_terminal -v
uv run python -m unittest discover -s tests
```

Expected: all tests pass. Record the exact count; never reuse the prior 874-test claim.

- [ ] **Step 2: Run comparable corrected-risk replays**

Use the existing runtime snapshot and identical dates/config for both runs:

```bash
uv run python scripts/run_replay_harness.py runtime --run-id duo_stoprisk_hold --start 2024-01-01 --end 2026-07-15 --portfolio-config backtests/configs/duo_ak_utbot.yaml --arbiter hold --merit btc_utbot_m15_h1,btc_ak_macd_4h
uv run python scripts/run_replay_harness.py runtime --run-id duo_stoprisk_topup --start 2024-01-01 --end 2026-07-15 --portfolio-config backtests/configs/duo_ak_utbot.yaml --arbiter topup --merit btc_utbot_m15_h1,btc_ak_macd_4h
uv run python scripts/run_replay_harness.py compare-arbiter --baseline duo_stoprisk_hold --candidate duo_stoprisk_topup
```

Expected: both runs complete from the same snapshot. Treat the old +391% result as legacy sizing, not as evidence for the corrected implementation.

- [ ] **Step 3: Reprice both decision streams under identical execution stress**

Run the project’s existing repricing entrypoint for 0, 2, 5 and 10 bps on both ledgers. Record skipped trades, return, maximum drawdown and net-by-strategy; any gap-through or unmatched tranche must be reported rather than hidden.

- [ ] **Step 4: Write the report with provenance**

Document commands, configuration, date range, test count, hold/topup tables, realistic execution table, limitations and SHA-256 hashes of the produced ledgers. State plainly whether corrected topup still improves the paper objective and never substitute legacy-sizing numbers.

- [ ] **Step 5: Review the final working-tree delta**

Run:

```bash
git diff -- orum/portfolio/paper_broker.py orum/portfolio/paper_engine.py scripts/replay_harness/arbiter.py scripts/replay_harness/arbiter_report.py scripts/replay_harness/reprice.py orum/dashboard.py orum/static/dashboard.js state/portfolio.yaml config/portfolio.yaml tests/test_paper_broker.py tests/test_paper_engine.py tests/test_arbiter.py tests/test_replay_harness.py tests/test_dashboard_terminal.py backtests/reports/chantier4_paper_topup_stop_risk.md
```

Expected: every added line traces to tranche identity, real stop risk, explicit auction, dashboard aggregation, configuration activation, tests or evidence. Do not commit if staging would mix pre-existing user changes.

### Task 8: Architecture and safety verification

**Files:**
- Verify only: all files above

- [ ] **Step 1: Run GitNexus change detection**

Run `gitnexus_detect_changes` for the whole change scope and inspect every changed symbol. Any HIGH or CRITICAL unexpected impact blocks completion until addressed.

- [ ] **Step 2: Confirm operational non-actions**

Verify from process metadata only that no worker, watcher, producer or dashboard was restarted by this work. Do not send a signal and do not invoke launchctl.

- [ ] **Step 3: Final evidence summary**

Report changed files, exact test/replay results, residual risks, and explicitly state that activation is on disk for the next paper cycle but that no process was restarted.
