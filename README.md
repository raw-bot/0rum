# hermes-trading

Paper-mode, self-improving trading worker. **No real orders are ever placed**:
positions and PnL are simulated against live Binance 1-minute candles, and a
reflection brain (LLM via the external `hermes` CLI, or a deterministic
fallback) adjusts one strategy variable at a time based on outcomes.

## Processes

| Process | Command | Role |
|---|---|---|
| Worker | `uv run python -m hermes_trading.run` | 60s loop: fetch data, RSI entry/exit, write trades and heartbeat |
| Watcher | `uv run python -m hermes_trading.hermes_watch` | every 30 min, runs an LLM reflection once 10 trades closed since the last one |
| Dashboard | `uv run python -m hermes_trading.dashboard` | http://127.0.0.1:8787 — read-only view + manual reflection button |

Run all three under supervision (auto-restart, Ctrl-C stops everything):

```bash
./scripts/run_local.sh
```

Run tests:

```bash
uv run python -m unittest discover -s tests
```

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
| `hermes_watcher.json` | watcher status |
| `events.jsonl` | persistent incident log: boots, failures, price-source flips, guardrail transitions, opens/closes, quarantines |
| `position_quarantine.jsonl` | positions discarded instead of traded (stale after outage, duplicate close after crash) |
| `.reflect.lock` | single-instance reflection lock (auto-expires after 300s) |
| `archive/` | snapshots of previous runs; never read by the code |

## Accounting model

All performance numbers are **account-level and fee-inclusive**: a trade's
return is `net_pnl_usd / balance_before` (see `hermes_trading/accounting.py`).
`pnl_pct` on a trade is the raw *price* move on the notional and must not be
compounded directly. Score, reflection input and the dashboard all share the
same helpers.

## Guardrails (enforced in the worker)

| Condition | Effect |
|---|---|
| price source = `offline_fallback` | entries **and** exits frozen (`offline_freeze`) |
| drawdown ≥ `max_drawdown` (5%) | no new entries (`guardrail_halt`) |
| drawdown ≥ `emergency_stop_drawdown` (6%) | open position closed (`emergency_stop`), all trading halted |
| open position older than `HERMES_MAX_POSITION_AGE_HOURS` (6h) | quarantined at startup, not traded |

Drawdown is a high-water metric over the whole `trades.jsonl`: once breached
it cannot recover on its own. **To resume after review**: create
`state/manual_resume.ok` (or archive/reset the trade history).

LLM reflections are bounded in code (`reflect.VARIABLE_BOUNDS`), subject to
the post-change cooldown (`cooldown_after_change_trades`), serialized by
`.reflect.lock`, and the dashboard button is rate-limited to one trigger per
minute.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `HERMES_TRADING_MODE` | `paper` | recorded on trades; nothing else reads it |
| `HERMES_LOOP_INTERVAL_SECONDS` | `60` | worker loop period |
| `HERMES_WATCH_INTERVAL_SECONDS` | `1800` | watcher poll period |
| `HERMES_MAX_POSITION_AGE_HOURS` | `6` | stale-position quarantine threshold |
| `HERMES_DASHBOARD_HOST` / `_PORT` | `127.0.0.1` / `8787` | dashboard bind |
| `HERMES_REFLECT_HOME` | `.sandbox/hermes-local-llm-home` | hermes CLI home for reflections |
| `GEMINI_API_KEY` | — | read from `<hermes home>/.gemini_api_key` if unset |

## Known limitations

- The strategy itself (1m RSI mean-reversion, RSI-55 exit) historically
  produced gross gains smaller than round-trip fees; the accounting now makes
  that visible, but fixing it is strategy research, not configuration.
- Stops/TP are evaluated once per loop on close prices: losses can exceed the
  stop; paper results are optimistic vs. real execution.
- `score()`'s "sharpe" term is a t-statistic, not an annualized Sharpe ratio.
- onchain/macro adapter data is recorded in the heartbeat but feeds no
  trading decision; news is `not_configured` without an API key.
- The Dockerfile only runs the worker; use `scripts/run_local.sh` locally.
