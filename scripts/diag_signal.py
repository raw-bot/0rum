"""Per-bar signal X-ray: WHY did / didn't the AK MACD brain fire on each recent
15m bar? Decomposes the EXACT production decision (compute_state + the candidate
machine predicates) condition-by-condition, and prints the official lifecycle
verdict (armed / confirmed / expired / rejected-why) for the last N closed bars.

Times are LOCAL. The forming bar is dropped (the bot only decides on closed bars).

Usage: uv run python scripts/diag_signal.py [N_bars_to_show] [fetch_count]
"""
from __future__ import annotations

import sys
from datetime import datetime

import baseline_ak_macd as base

from orum.external.ak_macd import (
    ACTION_CONFIRMED, AkMacdParams, compute_state,
    _flip_up, _flip_down, _strictly_increasing, _strictly_decreasing,
    _sequenced_long, _sequenced_short, _candle_in_direction, _regime_at,
    evaluate_ak_macd_verdict,
)


def ok(b: bool) -> str:
    return "✓" if b else "✗"


def main() -> None:
    show = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    fetch = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    p = AkMacdParams()

    raw = base.fetch_klines(n=fetch)
    bars = raw[:-1]                       # drop the forming bar -> last bar is closed
    st = compute_state(bars, p)
    n = len(bars)
    cts = [{**b, "ts": int(b["time"]) * 1000} for b in bars]

    print(f"Now (local): {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"Last closed 15m bar: {datetime.fromtimestamp(bars[-1]['time']):%Y-%m-%d %H:%M} "
          f"(opens; closes +15m)\n")
    print(f"params: confirm={p.confirmation_bars} window={p.candidate_window_bars} "
          f"regime={p.regime_filter} candle_gate={p.require_candle_direction}\n")

    for t in range(n - show, n):
        if t < 3:
            continue
        bar = bars[t]
        tloc = datetime.fromtimestamp(bar["time"])
        color = "blue" if st.is_blue[t] else "red" if st.is_red[t] else "gray"
        m0, m1, m2 = st.macd[t], st.macd[t - 1], st.macd[t - 2]
        fu, fd = _flip_up(st.macd, t), _flip_down(st.macd, t)
        vol_ok = st.volumes[t] > st.vol_ma[t] if st.vol_ma[t] == st.vol_ma[t] else False
        above = st.closes[t] > st.baseline[t]
        seqL, seqS = _sequenced_long(st, t, p), _sequenced_short(st, t, p)
        reg = _regime_at(st.closes, t)[0]
        green = st.closes[t] > st.opens[t] if st.opens[t] == st.opens[t] else None
        incr = _strictly_increasing(st.macd, t, p.confirmation_bars)
        decr = _strictly_decreasing(st.macd, t, p.confirmation_bars)
        verdict = evaluate_ak_macd_verdict(cts[: t + 1], p, symbol="BTCUSD")

        fired = "🔴 TRADE" if verdict.action == ACTION_CONFIRMED else ""
        print(f"── {tloc:%H:%M}  close {st.closes[t]:,.0f}  [{color}]  "
              f"vol {ok(vol_ok)}  regime={reg}  {fired}")
        print(f"     MACD: {m0:+.1f} (prev {m1:+.1f}, {m2:+.1f})  "
              f"flipUp {ok(fu)} flipDown {ok(fd)}  base {ok(above)}>0={ok(m0 > 0)}")
        # LONG requirements
        print(f"     LONG : macd>0 {ok(m0 > 0)} | close>base {ok(above)} | vol {ok(vol_ok)} | "
              f"seq(blue→pullback) {ok(seqL)} | candle-green {ok(bool(green))} | "
              f"macd↑×{p.confirmation_bars} {ok(incr)} | regime≠unfav {ok(reg != 'unfavorable')}")
        # SHORT requirements
        print(f"     SHORT: macd<0 {ok(m0 < 0)} | close<base {ok(not above)} | vol {ok(vol_ok)} | "
              f"seq(red→pullback) {ok(seqS)} | candle-red {ok(green is False)} | "
              f"macd↓×{p.confirmation_bars} {ok(decr)} | regime≠fav {ok(reg != 'favorable')}")
        print(f"     → brain: {verdict.action}"
              f"{f' ({verdict.side})' if verdict.side else ''}"
              f"{f'  win={verdict.remaining_window}' if verdict.remaining_window is not None else ''}"
              f"  — {verdict.reason or 'no setup'}")
        print()


if __name__ == "__main__":
    main()
