# Liquidation-Continuation Strategies on BTCUSDT — Research & Backtest Report

**Date:** 2026-07-01
**Hypothesis tested:** after a significant liquidation event, price continues in the
direction of the forced move rather than reversing.
**Result: 0 of 5 strategies survive the rejection criteria.** All five are
statistically and economically rejected after realistic fees/slippage, robustness
checks, and an out-of-sample chronological split. Details, mechanism, and an honest
accounting of *why* are below.

---

## 1. Research summary (full notes: `sources/LITERATURE_NOTES.md`)

**Serious sources used:**
- Jane et al., *"Liquidation, Leverage and Optimal Margin in Bitcoin Futures
  Markets,"* [arXiv:2102.04591](https://arxiv.org/abs/2102.04591) — BitMEX liquidation
  study: daily forced liquidations are 3.51%/1.89% of OI (long/short), liquidated
  accounts ran ~60x average leverage and "trade aggressively" once forced out. Does
  **not** test post-liquidation continuation — that gap motivated this project.
- Zeeshan Ali, *"Anatomy of the Oct 10–11, 2025 Crypto Liquidation Cascade,"* SSRN
  5611392 — single-event case study of a real ~$19B OI cascade; documents the
  leverage→liquidity→volatility feedback loop mechanism.
- *"Risk-Based Auto-Deleveraging"* (arXiv:2603.15963) and *"Autodeleveraging:
  Impossibilities and Optimization"* (arXiv:2512.01112) — exchange-mechanism papers
  on why cascades can overshoot via ADL.
- Binance public data documentation (github.com/binance/binance-public-data) — used
  directly as the data source (see §2).
- Neubert/Rams/Gruhn systematic literature review (SSRN 6639558) confirms there is
  **no settled academic literature validating liquidation-continuation as a tradeable
  edge** — the academic base is mechanism/risk papers, not strategy-performance papers.

**Weak sources (TradingView/Medium/exchange blogs), used only as idea sources, not
proof:** these were directly split between framing liquidation clusters as
*continuation magnets* and framing long/short liquidation imbalance as a
*mean-reversion / crowded-positioning* signal — i.e. retail commentary disagrees with
itself on the very question this project tests. That tension is exactly why this was
built and tested empirically rather than assumed.

**Conclusion from research:** the mechanical story for continuation (forced,
price-insensitive flow over a short window) is plausible and supported by real
leverage/liquidation magnitude data, but no source actually validates a tradeable
continuation rule. This is treated as an open, falsifiable hypothesis.

---

## 2. Data (full notes: `sources/DATA_NOTES.md`)

| Dataset | Symbol | Coverage | Source |
|---|---|---|---|
| 5m klines | BTCUSDT (USDT-M) | 2020-09-01 → 2026-06-29, 612,864 bars | Binance Data Vision (official) |
| 5m metrics (OI, top-trader L/S ratio, taker buy/sell vol ratio) | BTCUSDT | same range | Binance Data Vision (official, **real data**) |
| Funding rate | BTCUSDT | 2020-01 → 2026-05 | Binance Data Vision (official, **real data**) |
| Premium index (mark/index basis) | BTCUSDT | 2020-09 → 2026-06 | Binance Data Vision (official, **real data**) |
| **Real liquidation orders** | BTCUSD_PERP (COIN-M) | 2023-06-25 → 2024-10-14, 53,398 orders | Binance Data Vision (official, **real liquidation tape**) |

**Real liquidation data is only published by Binance for COIN-margined contracts**,
over a ~16-month window — confirmed by directly listing the S3 bucket; the USDT-M
`liquidationSnapshot` directory (where BTCUSDT would live) does not exist at all.
Following the brief's fallback instructions, a **clearly-labeled proxy** liquidation
signal was built and used for the main BTCUSDT backtest universe (5.8 years, multiple
regimes), and the proxy was **calibrated against the real BTCUSD_PERP tape** before
being trusted:

| Calibration check (real BTCUSD_PERP liquidations vs. proxy spikes, 2023-06→2024-10) | Result |
|---|---|
| Spearman corr(proxy intensity, real liquidation $) | **0.42** |
| P(real liquidation present in bar \| proxy spike fires) vs. base rate | **85.8% vs 13.8% → 6.2x lift** |
| E[real liquidation $ \| proxy spike] vs. unconditional | **18.0x lift** |
| Direction agreement (proxy candle direction vs. net liquidated side), top-decile real-liquidation bars | **83.0%** (50% = chance) |

The proxy meaningfully tracks real liquidation activity in both timing and direction.
It is not a perfect reconstruction (corr 0.42, not 1.0) — this is disclosed, not
hidden, and is the reason results below are described as testing a *liquidation-style
forced-flow proxy*, not literal liquidation data, for BTCUSDT.

**Proxy definition (shared base, all 5 strategies):** `proxy_spike` = a 5m bar where
both (a) traded volume is a rolling-24h z-score outlier and (b) the absolute bar
return is a rolling-24h z-score outlier, simultaneously. Direction = sign of the
bar's return (down-spike = proxy long-liquidation/forced-selling; up-spike = proxy
short-liquidation/forced-buying).

---

## 3. Backtest methodology

- **Execution:** signal computed from data known as of bar *t*'s close → position
  entered at bar *t+1*'s **open** (no lookahead). Stop/target computed from ATR known
  *before* the signal bar.
- **Exit:** ATR bracket — stop = 1.5×ATR(14, 5m), target = 2.0×ATR(14, 5m), measured
  intrabar against subsequent high/low; if both stop and target would be touched in
  the same bar, the **stop is assumed to win** (conservative). Hard timeout at 96 bars
  (8h) if neither is hit.
- **Costs:** 5 bps taker fee + 3 bps slippage **per side** (16 bps round trip total) —
  in line with Binance USDT-M non-VIP taker fees (4–5 bps) plus a slippage buffer for
  entries occurring immediately after a volatility spike (wider than normal-condition
  slippage).
- **Position sizing / equity curve:** fixed-fractional, 1% of equity risked per trade
  (R-multiple based), single position at a time, no pyramiding. *(Note: an earlier
  version of this engine compounded 100% of notional on every trade, which produces a
  mechanically near-total wipeout for any strategy with thousands of trades and even a
  marginally negative edge, regardless of whether the edge is real — this was caught
  and fixed before producing the results below; see engine source for detail.)*
- **Out-of-sample split:** chronological 70/30 (train ends 2024-09-29; test =
  2024-09-29 → 2026-06-29, ~21 months untouched).
- **Baseline:** 150-simulation Monte Carlo of random entries (same count, same
  long/short mix, identical exit rule and costs) — isolates whether *signal timing*
  adds value vs. just "trading this bracket in this market."
- **Rejection criteria applied automatically:** <50 trades; profit factor ≤ 1 after
  costs; fails to beat the random-entry baseline; result reverses after dropping the
  top-2 winning trades ("outlier dominance"); return sign flips across a 9–27-point
  parameter grid around the chosen thresholds ("not robust").

---

## 4. Results — all 5 strategies

### S1 — Liquidation spike + momentum confirmation
- **Entry:** `proxy_spike` AND the spike candle closes strongly in its own direction
  (close within the top/bottom 40% of the bar's range, direction-adjusted) — a
  "trend-day close" filter, not a spike-and-fade candle.
- **Exit / filters / params:** standard bracket (§3); `vol_z_th=2.0, ret_z_th=2.0,
  body_ratio_th=0.6`.
- **Trades:** 9,673 (4,856 long / 4,817 short). **Net return: −100%** (fixed-frac
  equity decays to ~0 — genuine negative edge compounded over thousands of trades, not
  an artifact; see §3 note). **Max DD: −100%. PF: 0.58. Win rate: 39.6%** (long 39.6%,
  short 39.5% — symmetric, rules out a direction-sign bug). **Expectancy: −0.0016/trade
  (−0.45R).**
- **OOS (test, n=3,019):** PF 0.48, win rate 37.2% — **worse** than in-sample, not better.
- **Baseline:** strategy return at the **0th percentile** vs. 150 random-entry sims
  (random mean PF 0.45) — strategy did not beat random.
- **Outlier check:** PF after dropping top-2 trades: 0.58 (unchanged) — not a lucky-tail
  result, just consistently negative.
- **Robustness:** 27-point grid (vol/ret/body thresholds ±15-20%): PF range **0.58–0.60,
  0% of combos profitable.**
- **Verdict: REJECT** — fails PF, baseline, and (trivially, since PF never exceeds 1)
  robustness.

### S2 — Long/short liquidation imbalance
- **Entry:** real (not proxy) Binance taker buy/sell volume-ratio z-score
  `|taker_ls_z| > 2.5`, gated by mildly elevated volume (`vol_z > 1.0`). Direction =
  sign of the imbalance (aggressive one-sided real taker flow).
- **Trades:** 356 (190 long / 166 short). **Net return: −89%. Max DD: −89%. PF: 0.51.
  Win rate: 37.4%.** **Expectancy: −0.61R.**
- **OOS (test, n=114):** PF 0.36, win rate 34.2% — also worse OOS.
- **Baseline:** strategy beat the random-mean (96th percentile) but the random
  baseline itself loses heavily (mean PF 0.45) on this bracket — "less bad than a
  losing baseline" is not a pass.
- **Outlier check:** PF after dropping top-2: 0.48 — still <1.
- **Robustness:** 9-point grid: PF range 0.48–0.69, **0% profitable.**
- **Verdict: REJECT.**

### S3 — Liquidation + open interest change
- **Entry:** `proxy_spike` AND concurrent abnormal **real OI contraction**
  (`oi_chg_z < -1.5`) — requires the spike to coincide with deleveraging (OI falling),
  not fresh momentum positioning (OI rising), to better distinguish a liquidation-style
  flush from an ordinary breakout.
- **Trades:** 2,046 (986 long / 1,060 short). **Net return: −99.97%. Max DD: −99.97%.
  PF: 0.65** (the best of the five, still well below 1). **Win rate: 40.6%**
  (long 38.3%, short 42.7%). **Expectancy: −0.38R.**
- **OOS (test, n=577):** PF 0.57, win rate 38.1%.
- **Baseline:** beat random mean (100th percentile) but again the random baseline is
  itself unprofitable on this bracket.
- **Outlier check:** PF after dropping top-2: 0.64 — robust to outliers, just genuinely
  unprofitable.
- **Robustness:** 27-point grid (vol/ret/OI thresholds): PF range 0.63–0.70, **0%
  profitable.**
- **Verdict: REJECT** — the OI-contraction filter is the *least bad* of the five (best
  PF and best win-rate-vs-breakeven gap) but still firmly unprofitable.

### S4 — Liquidation + volatility/volume expansion
- **Entry:** `proxy_spike` AND true-range expansion on top of the close-to-close move
  (`vol_z>2.5, ret_z>1.5, range_z>2.0`) — a realized-volatility regime-shift filter
  layered on the base spike.
- **Trades:** 9,673 (4,766 long / 4,907 short). **Net return: −100%. Max DD: −100%.
  PF: 0.61. Win rate: 40.3%.** **Expectancy: −0.43R.**
- **OOS (test, n=2,834):** PF 0.49, win rate 36.8%.
- **Baseline:** 0th percentile vs. random — did not beat baseline.
- **Outlier check:** PF after dropping top-2: 0.60 — unchanged.
- **Robustness:** 27-point grid: PF range 0.60–0.63, **0% profitable.**
- **Verdict: REJECT.**

### S5 — Liquidation near breakout / range edge / liquidity zone
- **Entry:** `proxy_spike` whose high/low **actually breaks** the prior rolling 8h
  high/low (a liquidity zone where resting stops/liquidations cluster) — i.e. spike +
  genuine level break, not just a spike in open space.
- **Trades:** 6,667 (3,311 long / 3,356 short). **Net return: −100%. Max DD: −100%.
  PF: 0.59. Win rate: 40.6%.** **Expectancy: −0.43R.**
- **OOS (test, n=1,943):** PF 0.47, win rate 37.2%.
- **Baseline:** 100th percentile vs. random, but again the random baseline itself loses
  on this bracket.
- **Outlier check:** PF after dropping top-2: 0.58 — unchanged.
- **Robustness:** 9-point grid: PF range 0.59–0.60, **0% profitable.**
- **Verdict: REJECT.**

---

## 5. Why all 5 failed — diagnosis, not excuse

Three separate pieces of evidence point to the same conclusion, and rule out the
most common causes of a *false* negative result:

1. **It is not a directional/sign bug.** Long and short win rates are symmetric
   within each strategy (e.g. S1: 39.6% long vs 39.5% short). A sign-flip bug would
   show one side strongly profitable and the other catastrophically not.
2. **It is not a bracket-calibration artifact.** Re-running S1/S4's exact signal
   population through a stop/target sweep (stop ∈ {1.5,2.5,3.5,5.0}×ATR, target ∈
   {1.5,2.5,4.0}×ATR) in **both** the continuation and the reversed (mean-reversion)
   direction never produced PF > 0.76 in any of 48 combinations tested. There is no
   nearby bracket configuration, in either direction, that turns this into a
   profitable setup.
3. **It is not luck/outliers.** Every strategy's profit factor is essentially
   unchanged after removing its two best trades, and 0% of every parameter-robustness
   grid point (99 combinations total across the five strategies) was profitable.

**The mechanism that appears to be defeating all five:** after a volume+volatility
spike, *realized* volatility over the next several bars tends to expand beyond what
the *pre-spike* ATR (computed from the 14 bars before the signal) predicted — visible
in the exit-reason breakdown, where stop-outs outnumber target-hits roughly 3:2 in
every strategy, and average winning trades are *smaller* than average losing trades
despite the bracket nominally favoring winners (2.0×ATR target vs. 1.5×ATR stop).
Even the **random-entry baseline** on this exact bracket has a mean PF of ~0.45 across
all five calibration runs — meaning a stop sized off pre-spike ATR is, by itself,
too tight for the volatility regime that typically follows a spike, independent of
any liquidation signal. Liquidation timing did not fix this; in three of five cases
it was statistically indistinguishable from or worse than random entries on the same
losing bracket, and in the other two it was "less bad than a baseline that also
loses," which does not constitute a tradeable edge.

This is consistent with — and helps adjudicate — the split in the weak/idea-tier
research sources (§1): the data here does not support the "liquidation clusters as
continuation magnets" framing for BTCUSDT 5m proxy spikes with this bracket
structure. It is silent on whether a *mean-reversion* edge exists with a properly
re-calibrated (wider, post-spike-aware) exit, since the reversed-direction sweep used
the same pre-spike-ATR bracket and also failed — that remains an open question for
future work, not something this brief asked for.

---

## 6. What would be needed to revisit this

- A stop/target model that re-estimates volatility **after** the spike confirms
  (e.g. ATR measured over the first 1–2 bars post-entry) rather than sizing off
  pre-spike ATR, to address the volatility-expansion-eats-the-stop mechanism in §5.
- Real liquidation tape for USDT-margined pairs (not available publicly at any
  reasonable cost/access level as of this writing) would remove the proxy-calibration
  uncertainty (corr 0.42, not 1.0) entirely.
- Testing on higher-beta alts (where liquidation cascades are anecdotally sharper
  relative to ADV) rather than BTCUSDT, which has the deepest liquidity and most
  efficient liquidation absorption of any perpetual market.

## Artifacts
- `sources/LITERATURE_NOTES.md`, `sources/DATA_NOTES.md` — full source list and data documentation
- `results/proxy_calibration.json`, `results/S1..S5_*.json` — full machine-readable results
- `src/` — download, feature engineering, backtest engine, strategy definitions, runner (all reproducible: `python src/download_data.py && python src/build_dataset.py && python src/run_strategy.py all`)
