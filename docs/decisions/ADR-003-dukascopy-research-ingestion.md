# ADR-003: Dukascopy Research Ingestion For XAUUSD

## Status
Accepted

## Date
2026-05-19

## Context

0rum needs cheap historical `XAUUSD` data for research and backtesting before paying for any premium data feed or advancing toward live execution. Local validation confirmed that Dukascopy public `.bi5` files work for `XAUUSD` without JForex, without an account, and without an API key.

Confirmed samples:

- 2015-01-02 09:00 UTC
- 2020-01-02 09:00 UTC
- 2020-01-06 through 2020-01-11
- 2026-05-11 through 2026-05-16

The observed `XAUUSD` price scale is `/1000`. M15, H1, H4, and D1 exports are supported from the tick cache.

## Decision

Use Dukascopy public `.bi5` as a research/backtest ingestion source for `XAUUSD`.

This decision does not alter runtime live market data, execution broker selection, or the separation between market data provider and execution broker.
The current Binance/CCXT `PAXG/USDT` path remains a runtime plumbing proxy only. Runtime proxy candles must be marked `source_kind="runtime_proxy"` and must not be accepted as evidence for optimizer parameter activation.

The ingestion workflow is:

1. plan guarded UTC batches
2. fetch batch data into local cache
3. run QA and classify expected market pauses, weekend closes, and suspicious gaps
4. import resampled OHLCV into PostgreSQL `candles` with provenance-preserving upserts

Research-origin candle rows produced by the Dukascopy loader persist provenance markers:

- `source_kind="research"`
- `research_source="dukascopy"`

The `candles` table stores these markers in nullable columns so optimizer
activation can verify source provenance after DB readback. Dukascopy imports
may upgrade existing unmarked or `runtime_proxy` rows for the same
`(instrument, timeframe, timestamp)` to research provenance; runtime proxy
fetches must not downgrade research rows.

## Non-Goals

- Do not install or depend on JForex.
- Do not require a Dukascopy account or API key.
- Do not treat Dukascopy as runtime live validation.
- Do not route orders through this ingestion path.
- Do not treat Binance/PAXG proxy candles as research-grade `XAUUSD`.

## Gap Policy

Market pauses are expected. The daily pause shifts with season and DST, so the QA layer must not encode a fixed `22:00 UTC` rule.

Initial classification policy:

- four consecutive missing M15 candles near end of session: `expected_market_pause`
- Friday close and weekend absence: `weekend_close`
- any other missing M15 candles: `suspicious_gap`

## Consequences

0rum can build multi-year research datasets progressively and resume safely from cache. The system remains cheap-validation-first: `research` before `paper_live`, and `paper_live` before `production_candidate`.

Execution broker selection remains separate from the Dukascopy historical ingestion decision.
Optimizer activation is intentionally blocked when provenance cannot be proven
from candle rows. Legacy rows created before provenance migration remain
untrusted until they are re-imported from Dukascopy or otherwise repaired.
