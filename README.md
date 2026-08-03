# 0rum-trading

Paper-mode, self-improving trading worker. **No real orders are ever placed**:
positions and PnL are simulated against live Binance 1-minute candles, and a
reflection brain (LLM via the external `0rum` CLI, or a deterministic
fallback) mutates the strategy based on outcomes — one structural change
(one DSL condition added/removed/modified) per reflection at most.

## Strategy DSL

`strategy.yaml` holds entry/exit **condition groups** interpreted by a pure
Python evaluator (`orum/dsl/`); the LLM emits JSON conditions,
never code:

```yaml
version: "04"
dsl_version: 1
entry:
  logic: AND            # AND | OR, flat, max 4 conditions
  conditions:
    - {indicator: rsi, params: {period: 14}, operator: "<=", value: 25}
    - {indicator: regime, operator: "!=", value_str: unfavorable}
exit:
  logic: OR
  conditions:
    - {indicator: rsi, params: {period: 14}, operator: ">=", value: 60}
risk:                   # enforced in loop.py, NOT mutable by the LLM
  stop_loss_pct: 2.0
  take_profit_pct: 3.0
  max_hold_candles: 30
  position_size_r: 0.5
direction: long          # v1: long-only, not mutable
```

Indicators (whitelist, TA-Lib semantics, tested against frozen TA-Lib
fixtures): `rsi`, `sma`, `ema`, `close`, `bollinger`, `atr`, `regime`.
Evaluation errors (insufficient warm-up, NaN) surface in the heartbeat and
`events.jsonl`; they are never silently swallowed.

Every LLM mutation passes a validation chain, each step able to reject:
jsonschema (strict) → semantic checks (param bounds, warm-up vs the
200-candle buffer) → `one_block_only` structural diff → a 7-day 1m-candle
non-regression backtest (zero signals, simulated daily-loss breach, or a
>10x trade-count explosion reject the proposal) → post-change cooldown.
A legacy (pre-DSL) `strategy.yaml` is migrated automatically at worker boot.

## Processes

| Process | Command | Role |
|---|---|---|
| Worker | `uv run python -m orum.run` | 60s loop: fetch data, evaluate DSL entry/exit, write trades and heartbeat |
| Watcher | `uv run python -m orum.orum_watch` | every 30 min, runs an LLM reflection once 10 trades closed since the last one |
| Dashboard | `uv run python -m orum.dashboard` | http://127.0.0.1:8787 — read-only view + manual reflection button |

Run all three under supervision (auto-restart, Ctrl-C stops everything):

```bash
./scripts/run_local.sh
```

Run tests:

```bash
uv run python -m unittest discover -s tests
```

## LLM trading laboratory (opt-in)

An isolated laboratory can ask an LLM (`nvidia/nemotron-3-ultra-550b-a55b`,
called directly against NVIDIA since 2026-07-25) for a French market brief
and two complete trade proposals. The model chooses direction,
size, leverage, stop, targets and time exit; every response is validated and
journaled with its evidence and snapshot hash. Confirmed `paper_autonomous`
runs execute accepted market actions only inside separate LLM paper accounts.

The default mode is `off`. One-shot observer and shadow runs are explicit:

```bash
export NVIDIA_API_KEY="..."
uv run python scripts/run_llm_lab.py --mode observer --provider nvidia --once
uv run python scripts/run_llm_lab.py --mode shadow --provider nvidia --once
uv run python scripts/run_llm_lab.py --mode paper_autonomous --provider nvidia --once --confirm-paper
```

Autonomous paper supports isolated long/short exposure, configurable 1x–40x
leverage, liquidation/SL/TP/time exits, deterministic outcomes, post-mortems
and bounded lessons. It never touches the native paper ledger or a real order
method. `paper_assisted` remains refused until a native/LLM merge contract is
specified. See [docs/llm-trading-lab.md](docs/llm-trading-lab.md) for the full
operator guide and offline replay.

## State files (`state/`)

| File | Content |
|---|---|
| `goal.yaml` | targets, drawdown thresholds, reflection policy (operator-owned) |
| `strategy.yaml` | current strategy; mutated only by reflections, version-bumped |
| `history/vNNNN.yaml` | pre-change snapshot of each strategy version |
| `trades.jsonl` | closed trades, append-only, with fee-inclusive `net_pnl_usd`, `account_return`, `balance_before/after_usd` |
| `hypotheses.jsonl` | every reflection outcome (changed or held, with reason/model) |
| `open_position.json` | the single open paper position, if any |
| `heartbeat.json` | last worker iteration (price, RSI, decision, drawdown, guardrail) — overwritten each loop |
| `orum_watcher.json` | watcher status |
| `events.jsonl` | persistent incident log: boots, failures, price-source flips, guardrail transitions, opens/closes, quarantines |
| `llm_market_briefs.jsonl` | append-only LLM market opinions, facts, scenarios and invalidations |
| `llm_decisions.jsonl` | proposals, validations, rejections and paper execution audit |
| `llm_paper_fills.jsonl` | isolated reference/evolving LLM paper fills |
| `llm_reference_account.json` / `llm_evolving_account.json` | separate experimental account snapshots |
| `llm_outcomes.jsonl` | deterministic closed-decision outcomes and counterfactuals |
| `llm_postmortems.jsonl` | bounded LLM process reviews tied to immutable outcomes |
| `llm_lessons.jsonl` | append-only candidate/active lesson events |
| `position_quarantine.jsonl` | positions discarded instead of traded (stale after outage, duplicate close after crash) |
| `candle_history.json` | local cache of 1m candles for the non-regression backtest |
| `.reflect.lock` | single-instance reflection lock (auto-expires after 300s) |
| `archive/` | snapshots of previous runs; never read by the code |

## Accounting model

All performance numbers are **account-level and fee-inclusive**: a trade's
return is `net_pnl_usd / balance_before` (see `orum/accounting.py`).
`pnl_pct` on a trade is the raw *price* move on the notional and must not be
compounded directly. Score, reflection input and the dashboard all share the
same helpers.

## Guardrails (enforced in the worker)

| Condition | Effect |
|---|---|
| price source = `offline_fallback` | entries **and** exits frozen (`offline_freeze`) |
| drawdown ≥ `max_drawdown` (5%) | no new entries (`guardrail_halt`) |
| drawdown ≥ `emergency_stop_drawdown` (6%) | open position closed (`emergency_stop`), all trading halted |
| open position older than `0RUM_MAX_POSITION_AGE_HOURS` (6h) | quarantined at startup, not traded |

Drawdown is a high-water metric over the whole `trades.jsonl`: once breached
it cannot recover on its own. **To resume after review**: create
`state/manual_resume.ok` (or archive/reset the trade history).

LLM reflections only touch the entry/exit DSL groups — the `risk:` block
(stops, sizing, hold limits) is not exposed to the model at all — and go
through the validation chain described above, subject to the post-change
cooldown (`cooldown_after_change_trades`), serialized by `.reflect.lock`;
the dashboard button is rate-limited to one trigger per minute.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ORUM_TRADING_MODE` | `paper` | recorded on trades; nothing else reads it |
| `0RUM_LOOP_INTERVAL_SECONDS` | `60` | worker loop period |
| `0RUM_WATCH_INTERVAL_SECONDS` | `1800` | watcher poll period |
| `0RUM_MAX_POSITION_AGE_HOURS` | `6` | stale-position quarantine threshold |
| `ORUM_DASHBOARD_HOST` / `_PORT` | `127.0.0.1` / `8787` | dashboard bind |
| `0RUM_REFLECT_HOME` | `.sandbox/0rum-local-llm-home` | 0rum CLI home for reflections |
| `GEMINI_API_KEY` | — | read from `<0rum home>/.gemini_api_key` if unset |
| `NVIDIA_API_KEY` | — | required for LLM observer/shadow/autonomous calls; never logged |

## Known limitations

- The starting strategy (1m RSI mean-reversion) historically produced gross
  gains smaller than round-trip fees. Reflection can now restructure the
  entry/exit conditions (indicators, regime filter, crosses) but not the
  risk levers; whether it converges to a profitable strategy is the
  experiment, not a guarantee. The deterministic fallback may still reduce
  `position_size_r` after a daily-loss breach.
- Stops/TP are evaluated once per loop on close prices: losses can exceed the
  stop; paper results are optimistic vs. real execution.
- `score()`'s "sharpe" term is a t-statistic, not an annualized Sharpe ratio.
- onchain/macro adapter data is recorded in the heartbeat but feeds no
  trading decision; news is `not_configured` without an API key.
- The Dockerfile only runs the worker; use `scripts/run_local.sh` locally.
- LLM limit proposals are visibly rejected until pending-order simulation is
  implemented; only market actions execute in the isolated LLM paper books.
- `paper_assisted` is intentionally unavailable, and passing an offline replay
  is not evidence of profitability or legal eligibility.
