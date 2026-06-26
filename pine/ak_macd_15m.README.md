# AK MACD 15m — external strategy (NOT ACTIVE)

A self-contained external strategy for HermesTrading, recreated from
`Docs/AXMACD/AX MACD Strat.md`. It is fully isolated from the native DSL
strategy and from `hermes_mirror.pine` — it shares no code and runs in a
different signal-source mode. **Nothing here is active.** The live native config
keeps running untouched until you explicitly activate it.

## What it does

Emits the strict Hermes external-signal JSON (`source/strategy/symbol/timeframe/
event/bar_time/price` + audit fields) on closed 15m bars. Hermes stays master:
it validates against the allowlist + risk gates and manages SL/TP. The Pine never
places an order.

### Strategy rules (long; short is the mirror, off by default)
1. **SSL Hybrid baseline (EMA 30)** blue + price above it → uptrend bias.
2. **Pullback**: baseline turns gray/red (price re-enters the continuation band).
3. **AK MACD BB** dots flip red→green **above** the zero line.
4. **Volume** bar above its MA(9).
→ all true on a closed bar = `BUY_CANDIDATE`. SL below baseline/swing, TP = 1.5R
(managed Hermes-side; the Pine does not emit EXIT by default).

## How to ACTIVATE later (3 steps — do NOT do these to keep current config running)

1. Set the mode in `state/goal.yaml`:
   ```yaml
   signal_source: tradingview_external
   ```
2. Make this the **only** active allowlist entry (no per-strategy ownership yet,
   so two active externals can step on each other). Comment out `hermes_mirror_v1`
   and add:
   ```yaml
   allowed_external_strategies:
     - id: "ak_macd_15m_v1"
       symbol: "BTCUSD"          # syminfo.ticker of the chart, match EXACT
       engine_asset: "BTC/USDT"  # venue the worker prices/risk-manages on
       timeframe: "15m"
       events: ["BUY_CANDIDATE", "EXIT"]
   ```
3. Load `pine/ak_macd_15m.pine` on the TradingView chart (BTCUSD 15m) and ensure
   the polling reader is feeding signals (backlog **P1 binding polling always-on**
   must be running for autonomous operation).

To go back: set `signal_source: native` (or remove the key) — instantly returns
to the current config.

## Calibration done (2026-06-16)

Calibrated against `Docs/AXMACD/*.png` and a live read on OANDA:EURUSD 15m:

- **`Dot color logic` → `slope` (set as default).** In every screenshot the AK
  MACD dots flip color at the wave's troughs/peaks (green = rising, red =
  falling), which is slope-based — not a signal crossing. Confirmed and baked in.
- **Logic verified on live data** via `data_get_pine_tables`: baseline coloring
  (close vs ±ATR band → blue/gray/red), MACD dots, above/below-zero, and the
  volume gate all compute correctly. The volume gate correctly rejects the
  still-forming bar (incomplete tick volume).
- **Band width `atrMult` = 0.2** gives a ±~1.2 pip gray zone around the EMA30 on
  EURUSD 15m — a realistic pullback-to-baseline window. Left as the default.

### Not yet pinned (needs forward observation, not screenshots)
- Bar-for-bar match to the historical screenshots was **not possible**: the free
  TradingView EURUSD 15m feed does not carry the Sep/Dec 2022 intraday history
  shown in the video.
- `atrMult` (gray-zone width), `trendLook`/`pullLook` (setup window) and the
  exact community-indicator internals are starting points. Tune them by watching
  live closed bars in paper before trusting the signal.

Forward-test in paper before any live consideration (live exchange is backlog P2,
gated on proven paper profitability).
