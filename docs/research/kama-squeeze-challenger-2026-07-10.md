# KAMA squeeze challenger — first falsification run

## Scope

- Market: Binance BTC/USDT
- Timeframe: 4h
- Bars: 17,000
- Approximate window: September 2018 through July 2026
- Chronological holdout: trades signalled from 2024 onward
- Long-only, one position at a time
- Entry: next bar open
- Fees: 0.1% round trip
- Slippage: 0.03% per fill
- Sizing: 0.5% planned account risk, 10% notional cap, 1x
- KAMA parameters: frozen in ADR-001
- AK-MACD parameters: existing defaults and validated long-only runner

This comparator is intentionally stricter than the historical AK validation:
it forbids overlapping positions, fills entries at the next open, and applies
the same account-risk and notional caps to both strategies. Therefore its AK
trade count and headline PF are not expected to reproduce the older report.

## Results

| Strategy | Full trades | Full PF | Full expectancy | Full max DD | Holdout trades | Holdout PF | Holdout expectancy |
|---|---:|---:|---:|---:|---:|---:|---:|
| KAMA squeeze | 97 | 1.64 | +0.361R | 2.97% | 28 | 0.83 | -0.091R |
| AK-MACD | 95 | 1.46 | +0.244R | 3.77% | 32 | 1.99 | +0.455R |

KAMA was profitable in-sample (69 trades, PF 1.96) but failed the untouched
2024+ holdout. Its yearly PF fell to 1.05 in 2024, 0.75 in 2025, and 0.55 in
2026. AK-MACD remained positive over the same holdout, although its sample is
still modest and 2021 was negative.

## Decision

Do not promote KAMA and do not add it to `state/portfolio.yaml`. Keep the engine
and comparator as an inactive research challenger. The full-window advantage is
not accepted because it disappears and reverses in the chronological holdout.

No parameter tuning follows this result. Any future KAMA experiment must be
pre-registered as a new hypothesis and evaluated against a new untouched period
or a different point-in-time market sample.
