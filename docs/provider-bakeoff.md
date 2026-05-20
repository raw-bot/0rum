# Provider Roles

Date: 2026-05-20

Current provider truth for this branch.

## Active Roles

- Runtime plumbing: Binance public API with `XAUUSD -> PAXG/USDT`.
- Research/backtest: Dukascopy public `.bi5` for real `XAUUSD`.
- Execution broker: not selected in this branch.

## Guardrails

- Binance/PAXG is plumbing only and must not validate strategy quality.
- Dukascopy is research/backtest only and must not be treated as a runtime live provider.
- Market data provider and execution broker remain separate concerns.

## Dukascopy Helper

```bash
./.venv/bin/python scripts/dukascopy_fetch.py probe --symbol XAUUSD --date 2026-05-18 --hour 9
./.venv/bin/python scripts/dukascopy_fetch.py range --symbol XAUUSD --start 2026-05-18T09:00:00Z --hours 1
./.venv/bin/python scripts/dukascopy_fetch.py batch-plan --symbol XAUUSD --start 2020-01-01T00:00:00Z --end 2020-02-01T00:00:00Z --batch-days 5 --max-days 31
./.venv/bin/python scripts/dukascopy_fetch.py batch-fetch --symbol XAUUSD --start 2020-01-01T00:00:00Z --end 2020-02-01T00:00:00Z --batch-days 5 --max-days 31 --timeframes M15 H1 H4 D1 --progress
./.venv/bin/python scripts/dukascopy_fetch.py qa --symbol XAUUSD --start 2020-01-01T00:00:00Z --end 2020-02-01T00:00:00Z --timeframe M15
./.venv/bin/python scripts/dukascopy_fetch.py import-postgres --symbol XAUUSD --start 2020-01-01T00:00:00Z --end 2020-02-01T00:00:00Z --timeframes M15 H1 H4 D1 --dry-run
```

## Validated Status

- Dukascopy public `.bi5` works for `XAUUSD` research/backtest ingestion.
- `XAUUSD` price scale is `/1000`.
- No JForex install, account, or API key is required.
- Daily market pauses shift with season/DST and must be classified as expected pauses when they match the end-of-session heuristic.
