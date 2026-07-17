# BTC UT Bot M15/H1 Direct Paper Design

## Goal

Run a second BTC/USDT paper sleeve alongside the existing `btc_ak_macd_4h`
sleeve in the same account. The new `btc_utbot_m15_h1` sleeve enters from
closed M15 UT Bot signals, filters entries with the closed H1 EMA(200), and is
shown on its own dashboard card.

## Decisions

- Paper only. No broker or live-order path is added.
- Preserve the existing H4 position and its exit-only configuration.
- The new sleeve is long-only: a qualified BUY opens its own position; a UT Bot
  SELL closes only that sleeve and never opens a short.
- Long trigger parameters are fixed at key `6`, ATR period `10`.
- Exit trigger parameters are fixed at key `7`, ATR period `20`.
- Entries require the latest closed H1 close to be strictly above H1 EMA(200).
- Indicators use ordinary OHLC close prices, Wilder ATR, closed candles only,
  and no Heikin-Ashi transformation.
- Each sleeve remains keyed by `strategy_id`, even when symbols are identical.

## Data flow

`PaperEngine` asks each engine for its declared `required_timeframes`. It fetches
the configured primary timeframe plus the declared secondary timeframes and
passes them in a backward-compatible `StrategyContext.candles_by_timeframe`.
Existing engines continue to receive their unchanged `context.candles` list.

The UT Bot engine evaluates the most recent closed M15 candle. It computes two
independent ATR trailing-stop recurrences: the long recurrence generates the
BUY crossover and the exit recurrence generates the SELL crossover. Repeated
evaluation of the same M15 timestamp returns no new signal.

## Shared portfolio risk

Both sleeves size from one equity snapshot. Before a new position is opened,
the engine enforces:

- a configurable total open stop-risk cap;
- a stricter configurable per-symbol stop-risk cap;
- inclusion of already-open positions and earlier fills from the same cycle.

The default new-sleeve risk is `0.005` (0.5% of equity). Existing positions are
never force-closed by the new cap; the cap blocks only new entries.

## Dashboard

The API exposes market cards by a stable card key rather than assuming one card
per symbol. The existing `BTC/USDT` H4 payload remains unchanged. A new
`BTC/USDT::btc_utbot_m15_h1` payload contains M15 candles, UT Bot markers, and
real fills filtered by `strategy_id`.

The frontend adds a second BTC card and resolves the displayed position by
`strategy_id`, preventing the H4 and M15/H1 positions or markers from being
mixed.

## Scheduling

The portfolio loop runs every 15 minutes. Every strategy receives only closed
candles and must deduplicate by candle timestamp. The H4 sleeve therefore keeps
its H4 semantics even though the orchestration cycle is more frequent.

## Verification

- Unit tests for the UT Bot recurrence, H1 filter, duplicate candle suppression,
  and SELL-as-exit signal.
- Paper-engine tests proving two BTC strategy IDs coexist and receive different
  timeframes.
- Broker/portfolio tests proving total and BTC-specific risk caps block only the
  extra entry.
- Dashboard tests proving two BTC payloads and strategy-filtered fills.
- Existing portfolio, strategy-registry, dashboard and full test suites remain
  green.

## Operational constraint

Code and configuration may be prepared in the live repository, but worker,
dashboard, watcher and producer processes are not restarted without separate
explicit operator confirmation.
