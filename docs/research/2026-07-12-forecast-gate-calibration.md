# Forecast gate — first live-data calibration

## Date and data

2026-07-12, latest 900 closed Binance spot candles at 1 hour for BTC/USDT,
ETH/USDT and PAXG/USDT. Evaluation uses 331 realized walk-forward forecasts per
horizon. No portfolio state or order was changed by this measurement.

## Result

The gate remains locked for all three assets. Coverage and sample count are
mostly acceptable, but the conditional nearest-regime forecast does not beat
the unconditional quantile baseline on pinball loss.

| Asset | Horizon | P10–P90 coverage | Direction | Pinball skill | Status |
|---|---:|---:|---:|---:|---|
| BTC/USDT | 12 h | 78.9% | 53.8% | -3.18% | locked |
| BTC/USDT | 24 h | 78.9% | 58.9% | -3.37% | locked |
| ETH/USDT | 12 h | 80.1% | 58.9% | -0.57% | locked |
| ETH/USDT | 24 h | 77.0% | 55.0% | -0.70% | locked |
| PAXG/USDT | 12 h | 78.2% | 50.8% | -4.37% | locked |
| PAXG/USDT | 24 h | 73.7% | 52.0% | -10.04% | locked |

## Interpretation

ETH is closest to qualification, confirming that its larger variation is not
by itself an edge: direction is usable, but the full predicted distribution is
still marginally worse than the naïve baseline. Thresholds must not be relaxed
after seeing this result. The next research iteration should improve features
or regime conditioning and re-run the same frozen evaluation contract.
