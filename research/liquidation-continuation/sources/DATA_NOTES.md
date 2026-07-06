# Data sources, coverage, and known limitations

All data is pulled directly from **data.binance.vision**, Binance's official public
historical data repository (documented at github.com/binance/binance-public-data),
served from a public S3 bucket — no API key, no third-party aggregator.

## Datasets used

| Dataset | Symbol / market | Coverage | Resolution | Use |
|---|---|---|---|---|
| `klines` | BTCUSDT, USDT-margined (um) perpetual | 2020-09-01 → 2026-06-29 (612,864 bars) | 5m | primary price/volume series |
| `metrics` | BTCUSDT (um) | 2020-09-01 → 2026-06-30 | 5m | **real** open interest, top-trader long/short ratio, taker buy/sell volume ratio |
| `fundingRate` | BTCUSDT (um) | 2020-01 → 2026-05 | ~8h events | **real** funding rate |
| `premiumIndexKlines` | BTCUSDT (um) | 2020-09-01 → 2026-06-28 | 5m | **real** mark/index premium (basis) |
| `liquidationSnapshot` | BTCUSD_PERP, COIN-margined (cm) | 2023-06-25 → 2024-10-14 (53,398 orders after de-dup) | event-level | **real liquidation orders**, used only for proxy calibration |
| `klines` (calibration) | BTCUSD_PERP (cm) | 2023-06-20 → 2024-10-20 | 5m | price series matching the real liquidation window |

## Why BTCUSDT has no real liquidation tape

Binance's `liquidationSnapshot` dataset is **only populated for COIN-margined (cm)
contracts**, and even there only for a subset of symbols/windows (e.g. BTCUSD_PERP:
2023-06-25 through 2024-10-14, then nothing afterward — confirmed by directly
listing the S3 bucket). The directory `data/futures/um/daily/liquidationSnapshot/`
(USDT-margined, where BTCUSDT lives) **does not exist at all** in the bucket — this
was verified directly (empty `ListBucketResult`, and the symbol is absent from
`um`'s root daily-dataset listing). This is a known, widely-discussed limitation of
Binance's public liquidation feed: it is a partial snapshot of the liquidation
engine's own order placements, not a full liquidation tape, and Binance has reduced
what it publishes over time. Full historical liquidation tapes exist only behind paid
aggregators (Coinglass, CryptoQuant, etc.), which are commercial products, not
verifiable public datasets, and were excluded per the brief's data-source rules.

## How this shaped the methodology

1. Real liquidation data for **BTCUSD_PERP (COIN-M)** was downloaded and used to
   **calibrate** a proxy liquidation-spike detector (see `calibrate_proxy.py` /
   `results/proxy_calibration.json`): proxy spikes show 6.2x lift in real-liquidation
   presence, 18x lift in real-liquidation $ magnitude, and 83% directional agreement
   (vs 50% chance) with which side was actually liquidated more, in the same 5m bar.
2. The proxy (abnormal volume + abnormal directional move, optionally combined with
   real OI, real taker buy/sell ratio, real funding, real premium) is then applied to
   **BTCUSDT (USDT-M)**, which has 5.8 years of continuous history (2020-09 → 2026-06)
   vs. only ~16 months for the real-tape symbol — giving enough sample size and
   regime diversity (2021 bull, 2022 bear/LUNA/FTX, 2023-25 recovery/chop) for a
   rejection-criteria-compliant backtest with 50+ trades and an out-of-sample split.
3. Every "liquidation" reference for the 5 main strategies is therefore explicitly a
   **proxy**, clearly labeled as such throughout REPORT.md — not a claim of using real
   liquidation event data for the main backtest universe.

## Data integrity notes

- Binance changed kline/metrics CSV header conventions across eras (some files have
  no header, some have a header with different column-name spellings for the same
  field, e.g. `taker_buy_vol` vs `taker_buy_volume`). The loader (`build_dataset.py`)
  forces positional column names across all eras after detecting header presence, so
  this does not silently corrupt the concatenated series.
- The `metrics` and `liquidationSnapshot` raw CSVs contain exact duplicate rows
  (every record appears twice) in every file inspected — de-duplicated before use.
- No price-level data integrity issues (bad ticks/outliers) were found to be driving
  the backtest results — verified by inspecting the worst individual trades, which
  are all bounded, plausible losses (worst single trade: -8.4% raw price return).
