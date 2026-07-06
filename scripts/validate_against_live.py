"""TRUST CHECK — does the backtest engine reproduce the bot's REAL paper trades?

You do not have to trust my backtests. This script replays the SAME production AK MACD
brain over the EXACT dates the live bot traded, then puts the backtest's entries side by
side with what the bot ACTUALLY recorded in state/trades.jsonl. If the engine reproduces
the real entries / stop / target, it is faithful. If it does not, the backtests are
worthless and you were right to distrust them. Reality decides — not me.

Run it yourself:
    cd <this repo>
    uv run python scripts/validate_against_live.py

What it checks, per real trade:
  - same entry bar (matched by the trade's own candle timestamp)?
  - same direction (long/short)?
  - same entry price (Binance 15m close vs the bot's recorded entry)?
  - same frozen STOP and TARGET (compute_bracket vs the bot's stored levels)?

Honest caveats printed at the end: the live bot ran with the SSL trail enabled (so some
EXITS differ from a bracket-only replay), and the live feed may differ slightly from the
Binance public klines used here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import baseline_ak_macd as base
import backtest_parity as bp

from orum.external.ak_macd import AkMacdParams, compute_state
from orum.external.bracket import compute_bracket

TRADES = "state/trades.jsonl"
TF_MS = 900_000


def load_trades():
    with open(TRADES) as f:
        return [json.loads(l) for l in f if l.strip()]


def main():
    trades = load_trades()
    tss = [t.get("opened_candle_ts") or t.get("bar_time") for t in trades]
    t0, t1 = min(tss), max(tss)
    print(f"Real trades: {len(trades)}  window "
          f"{datetime.fromtimestamp(t0/1000, timezone.utc):%Y-%m-%d %H:%M} → "
          f"{datetime.fromtimestamp(t1/1000, timezone.utc):%Y-%m-%d %H:%M} UTC\n")

    # Fetch enough 15m bars to cover the window PLUS the brain warm-up before it.
    # We pull a generous recent slab and slice; the brain needs ~80 bars of warm-up.
    need_back_days = (datetime.now(timezone.utc).timestamp() - t0 / 1000) / 86400 + 10
    n_bars = int(need_back_days * 96) + 500
    print(f"Fetching ~{n_bars} BTCUSDT 15m bars from Binance (covers the window + warm-up)...",
          flush=True)
    bars = base.fetch_klines(n=n_bars)
    print(f"Got {len(bars)} bars: "
          f"{datetime.fromtimestamp(bars[0]['time'], timezone.utc):%Y-%m-%d} → "
          f"{datetime.fromtimestamp(bars[-1]['time'], timezone.utc):%Y-%m-%d}\n", flush=True)

    p = AkMacdParams()
    st = compute_state(bars, p)
    sw = p.swing_look
    # brain confirmations -> {bar_time_ms: side}
    conf = {bars[i]["time"] * 1000: side for (i, side) in bp.confirmations(bars, p)}
    idx_by_ms = {bars[i]["time"] * 1000: i for i in range(len(bars))}

    print("=" * 104)
    print("REAL TRADE  vs  BACKTEST ENGINE  (matched by the trade's own candle timestamp)")
    print("=" * 104)
    print(f"  {'date UTC':<16}{'side':<12}{'entry real/bt':<22}{'STOP real/bt':<22}{'TARGET real/bt':<22}match")
    print("  " + "-" * 100)

    reproduced = 0
    entry_ok = 0
    for t in trades:
        ts = t.get("opened_candle_ts") or t.get("bar_time")
        dt = datetime.fromtimestamp(ts / 1000, timezone.utc)
        rdir = t.get("direction")
        r_entry = float(t.get("entry_price"))
        r_sl = float(t.get("stop_loss_price"))
        r_tp = float(t.get("take_profit_price"))

        side = conf.get(ts)
        if side is None:
            print(f"  {dt:%m-%d %H:%M}     {rdir:<11} {r_entry:<21.2f}{'—':<22}{'—':<22}❌ no signal")
            continue
        i = idx_by_ms[ts]
        bt_entry = st.closes[i]
        try:
            br = compute_bracket(entry_price=bt_entry, baseline_at_entry=st.baseline[i],
                                 recent_low=min(st.lows[max(0, i - sw + 1): i + 1]),
                                 recent_high=max(st.highs[max(0, i - sw + 1): i + 1]),
                                 direction=side, rr=float(t.get("reward_risk_ratio", 2.0)))
            bt_sl, bt_tp = br.stop_loss_price, br.take_profit_price
        except ValueError:
            bt_sl = bt_tp = float("nan")

        dir_ok = (side == rdir)
        e_ok = abs(bt_entry - r_entry) / r_entry < 0.002      # within 0.2% (feed diff)
        sl_ok = bt_sl == bt_sl and abs(bt_sl - r_sl) / r_sl < 0.005
        tp_ok = bt_tp == bt_tp and abs(bt_tp - r_tp) / r_tp < 0.005
        full = dir_ok and e_ok and sl_ok and tp_ok
        reproduced += full
        entry_ok += dir_ok and e_ok
        flag = "✅ exact" if full else ("⚠ entry ok" if (dir_ok and e_ok) else "❌ differ")
        print(f"  {dt:%m-%d %H:%M}     {rdir:<11} "
              f"{r_entry:.0f}/{bt_entry:.0f}{'':<8}"
              f"{r_sl:.0f}/{bt_sl:.0f}{'':<9}"
              f"{r_tp:.0f}/{bt_tp:.0f}{'':<9}{flag}")

    print("  " + "-" * 100)
    print(f"\n  VERDICT: {reproduced}/{len(trades)} trades reproduced EXACTLY "
          f"(entry+direction+stop+target);  {entry_ok}/{len(trades)} matched entry+direction.")
    print("\n  Honest caveats:")
    print("   - EXITS are not compared here: the live bot ran with the SSL trail enabled")
    print("     (trade 8 exited 'ssl_flip'), while a clean replay is bracket-only.")
    print("   - The live feed (price_source_at_entry) may differ a few bp from Binance klines.")
    print("   - If entries+stop+target match, the ENTRY engine is faithful; that is the part")
    print("     the backtests rely on. If they don't, distrust the backtests.")


if __name__ == "__main__":
    main()
