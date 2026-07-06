"""Long-horizon backtest of AK MACD 15m (long + short) on deep Binance history.

Fetches ~years of real BTCUSDT 15m klines from Binance public REST, replicates
ak_macd_15m.pine's indicator logic, and reuses the ENGINE's bracket.py for SL/TP.
Bracket-only exits (SL or TP), no max_hold — same contract as bracket.py.

Constraints honored:
  * paper/offline only (no orders placed anywhere)
  * NO parameter optimization (Pine defaults, frozen)
  * fees AND slippage included
  * long / short / combined reported separately
  * every bar where SL and TP are both inside [low, high] is FLAGGED
  * targets >= TARGET_TRADES resolved trades

Usage: uv run python scripts/backtest_ak_macd_long.py [n_bars]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone

from orum.external.bracket import compute_bracket

# ---- indicator params (ak_macd_15m.pine defaults — DO NOT tune) ----
MACD_FAST, MACD_SLOW, MACD_SIG = 12, 26, 9
BASE_LEN, ATR_LEN, ATR_MULT = 30, 14, 0.2
VOL_MA_LEN = 9
TREND_LOOK, PULL_LOOK, SWING_LOOK = 50, 10, 10
RR = 1.5
RISK_PCT = 0.02
START_EQUITY = 1000.0
FEE_PCT = 0.0004      # taker, per side (~0.08% round trip)
SLIP_PCT = 0.0003     # adverse slippage, per side
TARGET_TRADES = 150


def fetch_klines(symbol="BTCUSDT", interval="15m", n=50000):
    """Paginate Binance public klines backwards from now."""
    out: list[dict] = []
    end = int(time.time() * 1000)
    while len(out) < n:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
               f"&interval={interval}&limit=1000&endTime={end}")
        req = urllib.request.Request(url, headers={"User-Agent": "0rum-backtest"})
        with urllib.request.urlopen(req, timeout=30) as r:
            batch = json.loads(r.read().decode())
        if not batch:
            break
        rows = [{"time": k[0] // 1000, "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in batch]
        out = rows + out
        end = batch[0][0] - 1  # next page ends just before this batch's first bar
        time.sleep(0.25)
        if len(batch) < 1000:
            break
    return out[-n:]


def ema(xs, n):
    a = 2 / (n + 1); out = [xs[0]]
    for x in xs[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def rma(xs, n):
    a = 1 / n; out = [xs[0]]
    for x in xs[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def sma(xs, n):
    out = []
    for i in range(len(xs)):
        win = xs[max(0, i - n + 1): i + 1]
        out.append(sum(win) / len(win))
    return out


def true_range(h, l, c):
    tr = [h[0] - l[0]]
    for i in range(1, len(h)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    return tr


def barssince(flags, i):
    for k in range(i, -1, -1):
        if flags[k]:
            return i - k
    return 10**9


def main():
    n_target = int(sys.argv[1]) if len(sys.argv) > 1 else 50000
    print(f"Fetching ~{n_target} BTCUSDT 15m bars from Binance ...", flush=True)
    bars = fetch_klines(n=n_target)
    h = [b["high"] for b in bars]; l = [b["low"] for b in bars]
    c = [b["close"] for b in bars]; v = [b["volume"] for b in bars]; t = [b["time"] for b in bars]
    n = len(c)
    print(f"Got {n} bars: {datetime.fromtimestamp(t[0], timezone.utc):%Y-%m-%d} "
          f"→ {datetime.fromtimestamp(t[-1], timezone.utc):%Y-%m-%d}\n", flush=True)

    macd = [a - b for a, b in zip(ema(c, MACD_FAST), ema(c, MACD_SLOW))]
    base = ema(c, BASE_LEN)
    atr = rma(true_range(h, l, c), ATR_LEN)
    vma = sma(v, VOL_MA_LEN)
    isBlue = [c[i] > base[i] + atr[i] * ATR_MULT for i in range(n)]
    isRed = [c[i] < base[i] - atr[i] * ATR_MULT for i in range(n)]
    isGray = [not isBlue[i] and not isRed[i] for i in range(n)]
    grayOrRed = [isGray[i] or isRed[i] for i in range(n)]
    grayOrBlue = [isGray[i] or isBlue[i] for i in range(n)]
    lowN = [min(l[max(0, i - SWING_LOOK + 1): i + 1]) for i in range(n)]
    highN = [max(h[max(0, i - SWING_LOOK + 1): i + 1]) for i in range(n)]

    trades = []
    same_bar_flags = 0
    i = 2
    while i < n - 1:
        flipUp = macd[i] > macd[i - 1] and macd[i - 1] <= macd[i - 2]
        flipDown = macd[i] < macd[i - 1] and macd[i - 1] >= macd[i - 2]
        volOk = v[i] > vma[i]
        buy = (barssince(isBlue, i) < TREND_LOOK and barssince(grayOrRed, i) < PULL_LOOK
               and c[i] > base[i] and flipUp and macd[i] > 0 and volOk)
        sell = (barssince(isRed, i) < TREND_LOOK and barssince(grayOrBlue, i) < PULL_LOOK
                and c[i] < base[i] and flipDown and macd[i] < 0 and volOk)
        if not (buy or sell):
            i += 1; continue
        direction = "long" if buy else "short"
        entry_sig = c[i]
        try:
            br = compute_bracket(entry_price=entry_sig, baseline_at_entry=base[i],
                                 recent_low=lowN[i], recent_high=highN[i],
                                 direction=direction, rr=RR)
        except ValueError:
            i += 1; continue

        # adverse entry slippage
        entry = entry_sig * (1 + SLIP_PCT) if direction == "long" else entry_sig * (1 - SLIP_PCT)
        sl, tp = br.stop_loss_price, br.take_profit_price
        risk_usd = START_EQUITY * RISK_PCT  # fixed-fractional on starting equity (no compounding bias)
        qty = risk_usd / br.risk_distance

        outcome, exit_i, exit_fill, same_bar = None, None, None, False
        for j in range(i + 1, n):
            hit_sl = (l[j] <= sl) if direction == "long" else (h[j] >= sl)
            hit_tp = (h[j] >= tp) if direction == "long" else (l[j] <= tp)
            if hit_sl and hit_tp:
                same_bar = True; same_bar_flags += 1
            if hit_sl:  # SL assumed first when both touch the same bar (conservative)
                exit_fill = sl * (1 - SLIP_PCT) if direction == "long" else sl * (1 + SLIP_PCT)
                outcome = "loss"; exit_i = j; break
            if hit_tp:
                exit_fill = tp  # limit fills at price, no favorable slippage assumed
                outcome = "win"; exit_i = j; break

        if outcome is None:
            trades.append({"dir": direction, "t": t[i], "open": True}); break

        gross = (exit_fill - entry) * qty if direction == "long" else (entry - exit_fill) * qty
        fees = FEE_PCT * (entry + exit_fill) * qty
        pnl = gross - fees
        trades.append({"dir": direction, "t": t[i], "entry": entry, "sl": sl, "tp": tp,
                       "outcome": outcome, "pnl": pnl, "r": pnl / risk_usd,
                       "exit_t": t[exit_i], "same_bar": same_bar, "open": False})
        i = exit_i + 1

    closed = [x for x in trades if not x.get("open")]

    def report(name, g):
        if not g:
            print(f"  {name:<9}: no trades"); return
        wins = [x for x in g if x["outcome"] == "win"]
        losses = [x for x in g if x["outcome"] == "loss"]
        gross_win = sum(x["pnl"] for x in wins)
        gross_loss = -sum(x["pnl"] for x in losses)
        pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
        net = sum(x["pnl"] for x in g)
        net_r = sum(x["r"] for x in g)
        print(f"  {name:<9}: {len(g):>4} trades | win {len(wins)/len(g):>5.1%} "
              f"({len(wins)}W/{len(losses)}L) | net {net_r:+7.1f}R  ${net:+9.2f} | PF {pf:.2f}")

    print(f"Resolved trades: {len(closed)}  (target >= {TARGET_TRADES})")
    print(f"Fees {FEE_PCT:.2%}/side, slippage {SLIP_PCT:.2%}/side, RR {RR}, risk {RISK_PCT:.0%}\n")
    report("LONG", [x for x in closed if x["dir"] == "long"])
    report("SHORT", [x for x in closed if x["dir"] == "short"])
    report("COMBINED", closed)
    print(f"\n  SL+TP same-bar (ambiguous, SL assumed): {same_bar_flags} "
          f"({same_bar_flags/len(closed):.1%} of resolved)" if closed else "")
    eq = START_EQUITY + sum(x["pnl"] for x in closed)
    print(f"  Final equity (fixed-fractional): ${eq:.2f} from ${START_EQUITY:.0f} "
          f"({eq/START_EQUITY-1:+.1%})")
    if len(closed) < TARGET_TRADES:
        print(f"\n  ⚠ only {len(closed)} resolved trades (< {TARGET_TRADES}); rerun with more bars.")


if __name__ == "__main__":
    main()
