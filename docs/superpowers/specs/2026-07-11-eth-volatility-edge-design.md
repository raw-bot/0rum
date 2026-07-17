# ETH Volatility Edge Research Design

## Objective

Determine whether ETH/EUR's higher realized volatility can support a repeatable
long-only spot edge that the daily Donchian 20/10 benchmark misses. This is a
paper-research phase only: the study must never modify portfolio configuration,
positions, fills, balances, or execution mode.

## Data design

Use Coinbase Exchange public daily BTC/EUR and ETH/EUR candles for long history.
The official endpoint permits at most 300 candles per request, so retrieval is
explicitly paginated and rate limited. Normalize candles oldest-to-newest and
reject duplicates, malformed OHLC, non-finalized bars, and non-daily timestamps.
Intervals absent from the venue response remain absent rather than being filled
with invented prices.

Use Kraken public daily BTC/EUR and ETH/EUR candles for the latest 720 finalized
bars. On their common timestamps, compare daily log returns. The long Coinbase
history is acceptable for inference only if both assets have return correlation
of at least 0.995 and median absolute return difference no greater than 0.20%.
Failure of either gate makes the report data-source-sensitive and non-activatable.

Official sources:

- https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles
- https://docs.kraken.com/api-reference/market-data/get-ohlc-data
- https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data

## Candidate strategy

Keep Donchian 20/10 as the unchanged benchmark. The ETH challenger enters long
at the next daily open only when all conditions are confirmed at the preceding
close:

1. ETH closes above its prior 20-day closing high.
2. Wilder ATR14 is greater than its value five sessions earlier.
3. ETH closes above SMA100 and SMA100 is above its value five sessions earlier.
4. ETH/BTC closes above its SMA50.

Exit at the next open after ETH closes below its prior 10-day closing low. Allow
one position at a time, charge 0.10% round trip plus 0.03% slippage per fill,
normalize every trade by `2 * ATR14` at the entry signal, and do not force-close
an open trade at the end of a sample. These parameters are frozen before seeing
the long-history result and are not optimized in this phase.

## Validation

Use expanding walk-forward evaluation: at least 730 calendar-aligned daily bars
of warm-up/training context, followed by non-overlapping 365-bar test windows.
Indicators may use earlier history, while each trade belongs to the test window
containing its entry signal. Do not tune on any test window.

The challenger remains research-only unless all gates pass:

- at least 20 closed out-of-sample trades;
- profit factor greater than 1.25;
- expectancy greater than +0.15R;
- positive expectancy in at least 60% of eligible test windows;
- maximum realized drawdown no greater than 8R;
- expectancy strictly greater than the unchanged Donchian benchmark;
- Coinbase/Kraken portability gates pass for both BTC/EUR and ETH/EUR.

Passing these gates would authorize a separate activation review, not automatic
portfolio activation.

## Runtime restart

Before restart, snapshot hashes and contents of paper positions and fills. Use
the installed launchd jobs as the only supervisors: restart `com.0rum.engine`
with `launchctl kickstart -k`, then kick `com.0rum.paper` once so it reloads
`state/portfolio.yaml`. Do not start a manual `run_engine.sh` or portfolio loop,
and do not restart the dashboard. Afterward verify one engine supervisor, worker,
watcher and producer chain, unchanged persisted positions, no unexpected fill,
and clean paper logs.

