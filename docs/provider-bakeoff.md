# Cheap Validation Plan

Date: 2026-05-18

This replaces the earlier "find the best provider" framing. 0rum should first
prove the bot cheaply, then pay for data only when the evidence justifies it.

## Modes

### research

Default mode.

Use:

- Dukascopy public `.bi5` datafeed
- CSV / HistData-style local files
- Massive free endpoints only if validated
- local cache
- replay
- backtests
- detailed decision logs

Do not use:

- real orders
- paid data subscriptions
- live-trading conclusions

Local CSV helper:

```bash
./.venv/bin/python scripts/research_csv_loader.py qa path/to/ohlcv.csv --timeframes D1
```

Dukascopy helper:

```bash
./.venv/bin/python scripts/dukascopy_fetch.py probe --symbol XAUUSD --date 2026-05-18 --hour 9
./.venv/bin/python scripts/dukascopy_fetch.py range --symbol XAUUSD --start 2026-05-18T09:00:00Z --hours 1
./.venv/bin/python scripts/dukascopy_fetch.py batch-plan --symbol XAUUSD --start 2020-01-01T00:00:00Z --end 2020-02-01T00:00:00Z --batch-days 5 --max-days 31
./.venv/bin/python scripts/dukascopy_fetch.py batch-fetch --symbol XAUUSD --start 2020-01-01T00:00:00Z --end 2020-02-01T00:00:00Z --batch-days 5 --max-days 31 --timeframes M15 H1 H4 D1 --progress
./.venv/bin/python scripts/dukascopy_fetch.py qa --symbol XAUUSD --start 2020-01-01T00:00:00Z --end 2020-02-01T00:00:00Z --timeframe M15
./.venv/bin/python scripts/dukascopy_fetch.py import-postgres --symbol XAUUSD --start 2020-01-01T00:00:00Z --end 2020-02-01T00:00:00Z --timeframes M15 H1 H4 D1 --dry-run
```

Validated status:

- Dukascopy public `.bi5` works for `XAUUSD` research/backtest ingestion.
- `XAUUSD` price scale is `/1000`.
- No JForex install, account, or API key is required.
- Dukascopy is not the runtime live provider and does not change execution broker selection.
- Stooq is not part of the current XAUUSD research path.
- Capital.com and cTrader remain candidates for future `paper_live` or execution research, separate from historical data.
- Daily market pauses shift with season/DST and must be classified as expected pauses when they match the end-of-session heuristic.

### paper_live

Use:

- Alpaca Free paper trading for US equities event-loop validation
- IEX/free market data only as a development feed
- dashboard and risk engine soak tests

Do not conclude:

- strategy is profitable in real conditions
- data is canonical
- XAUUSD strategy quality is proven

### production_candidate

Use only after research + paper_live evidence is strong.

Requires:

- 30 days of stable paper/live-like operation
- clean out-of-sample report
- fees/slippage modeled
- risk/execution defects closed
- kill switch mandatory

## Budget Rule

No market-data subscription above `20 EUR/month` until the project has proof that
better data is the current bottleneck.

## Current Provider Roles

| Provider | Role Now | Not For |
|---|---|---|
| Dukascopy public datafeed | XAUUSD research/backtest data | live validation |
| CSV files | offline research/backtest data | live validation |
| HistData local archives | offline historical bootstrap | runtime |
| Massive free | research candidate if REST/Flat Files access works | live intraday trading |
| Twelve Data free | tiny sanity checks only | canonical source |
| Alpaca Free | paper_live broker/API/event loop | canonical XAUUSD data |
| Paid Massive/Databento/etc. | later production candidate only | current phase |

## Massive Probe Status

Flat Files S3:

- bucket listing works
- forex minute prefix exists
- object downloads currently return `403 NOT_AUTHORIZED`

So Massive Flat Files are not usable yet with the current key/permission set.

## Alpaca Next Step

When we enter `paper_live`, add a separate Alpaca paper adapter. It should test:

- account connection
- clock/calendar
- submit/cancel paper order
- order updates
- position reconciliation
- dashboard visibility

It must not be used as proof that a real `XAUUSD` strategy is profitable.
