"""Offline backtest of the AK MACD 15m strategy (long + short) on real bars.

Replicates ak_macd_15m.pine's indicator logic in Python and reuses the ENGINE's
own orum/external/bracket.py to compute SL/TP, so the result reflects
exactly what 0rum would do once allow_short is enabled. Bracket-only exits
(SL or TP), no max_hold — same contract as bracket.py.

Usage:
    uv run python scripts/backtest_ak_macd.py <ohlcv.json>

<ohlcv.json> is the MCP data_get_ohlcv payload (list[{"type","text"}] or a raw
{"bars":[...]} object or a bare list of bars).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from orum.external.bracket import compute_bracket

# ---- indicator params (ak_macd_15m.pine defaults) ----
MACD_FAST, MACD_SLOW, MACD_SIG = 12, 26, 9
BASE_LEN, ATR_LEN, ATR_MULT = 30, 14, 0.2
VOL_MA_LEN = 9
TREND_LOOK, PULL_LOOK, SWING_LOOK = 50, 10, 10
RR = 1.5
RISK_PCT = 0.02
START_EQUITY = 1000.0
FEE_PCT = 0.0008  # round-trip ~0.08% (matches 0rum $2 on $2500 notional)


def ema(xs: list[float], n: int) -> list[float]:
    a = 2 / (n + 1)
    out = [xs[0]]
    for x in xs[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def rma(xs: list[float], n: int) -> list[float]:
    a = 1 / n
    out = [xs[0]]
    for x in xs[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def sma(xs: list[float], n: int) -> list[float]:
    out = []
    for i in range(len(xs)):
        lo = max(0, i - n + 1)
        win = xs[lo : i + 1]
        out.append(sum(win) / len(win))
    return out


def true_range(h, l, c):
    tr = [h[0] - l[0]]
    for i in range(1, len(h)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    return tr


def lowest(xs, n, i):
    return min(xs[max(0, i - n + 1) : i + 1])


def highest(xs, n, i):
    return max(xs[max(0, i - n + 1) : i + 1])


def barssince(flags: list[bool], i: int) -> int:
    for k in range(i, -1, -1):
        if flags[k]:
            return i - k
    return 10**9


def load_bars(path: str) -> list[dict]:
    raw = json.load(open(path))
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "text" in raw[0]:
        raw = json.loads(raw[0]["text"])
    if isinstance(raw, dict):
        raw = raw["bars"]
    return raw


def main() -> None:
    bars = load_bars(sys.argv[1])
    o = [b["open"] for b in bars]
    h = [b["high"] for b in bars]
    l = [b["low"] for b in bars]
    c = [b["close"] for b in bars]
    v = [b["volume"] for b in bars]
    t = [b["time"] for b in bars]
    n = len(c)

    macd = [a - b for a, b in zip(ema(c, MACD_FAST), ema(c, MACD_SLOW))]
    sig = ema(macd, MACD_SIG)  # noqa: F841 - kept for parity/debug
    base = ema(c, BASE_LEN)
    atr = rma(true_range(h, l, c), ATR_LEN)
    vma = sma(v, VOL_MA_LEN)

    isBlue = [c[i] > base[i] + atr[i] * ATR_MULT for i in range(n)]
    isRed = [c[i] < base[i] - atr[i] * ATR_MULT for i in range(n)]
    isGray = [not isBlue[i] and not isRed[i] for i in range(n)]
    grayOrRed = [isGray[i] or isRed[i] for i in range(n)]
    grayOrBlue = [isGray[i] or isBlue[i] for i in range(n)]

    trades = []
    i = 2
    while i < n - 1:
        flipUp = macd[i] > macd[i - 1] and macd[i - 1] <= macd[i - 2]
        flipDown = macd[i] < macd[i - 1] and macd[i - 1] >= macd[i - 2]
        volOk = v[i] > vma[i]
        wasBull = barssince(isBlue, i) < TREND_LOOK
        wasBear = barssince(isRed, i) < TREND_LOOK
        pulledLong = barssince(grayOrRed, i) < PULL_LOOK
        pulledShort = barssince(grayOrBlue, i) < PULL_LOOK

        buy = wasBull and pulledLong and c[i] > base[i] and flipUp and macd[i] > 0 and volOk
        sell = wasBear and pulledShort and c[i] < base[i] and flipDown and macd[i] < 0 and volOk
        if not (buy or sell):
            i += 1
            continue

        direction = "long" if buy else "short"
        entry = c[i]
        try:
            br = compute_bracket(
                entry_price=entry,
                baseline_at_entry=base[i],
                recent_low=lowest(l, SWING_LOOK, i),
                recent_high=highest(h, SWING_LOOK, i),
                direction=direction,
                rr=RR,
            )
        except ValueError:
            i += 1
            continue

        # simulate bracket exit on subsequent bars (SL first if both hit a bar)
        outcome, exit_i = None, None
        for j in range(i + 1, n):
            if direction == "long":
                if l[j] <= br.stop_loss_price:
                    outcome = -1.0
                elif h[j] >= br.take_profit_price:
                    outcome = RR
            else:
                if h[j] >= br.stop_loss_price:
                    outcome = -1.0
                elif l[j] <= br.take_profit_price:
                    outcome = RR
            if outcome is not None:
                exit_i = j
                break

        trades.append(
            {
                "dir": direction,
                "entry_t": t[i],
                "entry": entry,
                "sl": br.stop_loss_price,
                "tp": br.take_profit_price,
                "r": outcome,
                "exit_t": t[exit_i] if exit_i else None,
                "open": outcome is None,
            }
        )
        # one position at a time: resume after the exit bar
        i = (exit_i + 1) if exit_i else (i + 1)

    # ---- report ----
    def fmt(ts):
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%m-%d %H:%M") if ts else "—"

    eq = START_EQUITY
    print(f"\nBars: {n}  ({fmt(t[0])} → {fmt(t[-1])} UTC)  | RR={RR} risk={RISK_PCT:.0%}\n")
    print(f"{'dir':<6}{'entry@UTC':<13}{'entry':>9}{'SL':>9}{'TP':>9}{'R':>6}  result")
    closed = [x for x in trades if not x["open"]]
    for x in trades:
        if x["open"]:
            res = "OPEN (unresolved)"
            rtxt = "—"
        else:
            risk_usd = eq * RISK_PCT
            pnl = x["r"] * risk_usd - FEE_PCT * (risk_usd / abs(x["entry"] - x["sl"]) * x["entry"])
            eq += pnl
            res = f"{'WIN ' if x['r'] > 0 else 'LOSS'} exit {fmt(x['exit_t'])}  pnl ${pnl:+.2f}  eq ${eq:.2f}"
            rtxt = f"{x['r']:+.1f}"
        print(f"{x['dir']:<6}{fmt(x['entry_t']):<13}{x['entry']:>9.0f}{x['sl']:>9.0f}{x['tp']:>9.0f}{rtxt:>6}  {res}")

    longs = [x for x in closed if x["dir"] == "long"]
    shorts = [x for x in closed if x["dir"] == "short"]

    def stats(g):
        if not g:
            return "no trades"
        wins = sum(1 for x in g if x["r"] > 0)
        tot_r = sum(x["r"] for x in g)
        return f"{len(g)} trades, {wins}/{len(g)} wins ({wins/len(g):.0%}), net {tot_r:+.1f}R"

    print(f"\n  LONG : {stats(longs)}")
    print(f"  SHORT: {stats(shorts)}")
    print(f"  ALL  : {stats(closed)}   |  open/unresolved: {sum(1 for x in trades if x['open'])}")
    print(f"  Final equity: ${eq:.2f} (start ${START_EQUITY:.0f})\n")


if __name__ == "__main__":
    main()
