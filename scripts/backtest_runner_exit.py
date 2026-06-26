"""Stage-1 backtest: fixed-2R bracket vs RUNNER exit, on the SAME AK MACD entries.

Isolates the EXIT change. Every AK MACD signal is taken once (no one-at-a-time
de-overlap) so both engines trade the identical entry set -> apples-to-apples.
Conservative intrabar assumptions on 15m (the adverse extreme is assumed to hit
the stop BEFORE this bar's favorable high extends/raises the trail), so the runner
numbers are a PESSIMISTIC lower bound. If it still beats fixed-2R here, it's real.

Runner rule (spec ak-macd-runner-exit.md):
  SL initial -> once +1.5R touched, floor stop at +1.5R (never give back below)
             -> once +2R touched, runner mode: trail = peak - k*ATR, floored at +1.5R,
                ratcheting (monotonic), NO upper cap. Exit only when the stop is hit.

Usage: uv run python scripts/backtest_runner_exit.py [n_bars]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone

from hermes_trading.external.bracket import compute_bracket

MACD_FAST, MACD_SLOW, MACD_SIG = 12, 26, 9
BASE_LEN, ATR_LEN, ATR_MULT = 30, 14, 0.2
VOL_MA_LEN = 9
TREND_LOOK, PULL_LOOK, SWING_LOOK = 50, 10, 10
RR = 2.0              # baseline take-profit, matches live strategy.yaml
FEE_PCT = 0.0004
SLIP_PCT = 0.0003
FLOOR_R = 1.5
RUNNER_R = 2.0
K_VALUES = [2.0, 2.5, 3.0]


def fetch_klines(symbol="BTCUSDT", interval="15m", n=50000):
    out: list[dict] = []
    end = int(time.time() * 1000)
    while len(out) < n:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
               f"&interval={interval}&limit=1000&endTime={end}")
        req = urllib.request.Request(url, headers={"User-Agent": "hermes-backtest"})
        with urllib.request.urlopen(req, timeout=30) as r:
            batch = json.loads(r.read().decode())
        if not batch:
            break
        rows = [{"time": k[0] // 1000, "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in batch]
        out = rows + out
        end = batch[0][0] - 1
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
    return [sum(xs[max(0, i - n + 1): i + 1]) / len(xs[max(0, i - n + 1): i + 1]) for i in range(len(xs))]


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


def sim_fixed(direction, entry, sl, tp, risk, h, l, start):
    """Fixed 2R bracket, conservative SL-first. Returns R (net of fees+slip) or None."""
    n = len(h)
    for j in range(start, n):
        hit_sl = (l[j] <= sl) if direction == "long" else (h[j] >= sl)
        hit_tp = (h[j] >= tp) if direction == "long" else (l[j] <= tp)
        if hit_sl:
            fill = sl * (1 - SLIP_PCT) if direction == "long" else sl * (1 + SLIP_PCT)
            return _r(direction, entry, fill, risk)
        if hit_tp:
            return _r(direction, entry, tp, risk)
    return None


def sim_runner(direction, entry, sl_init, risk, h, l, atr, start, k):
    """Floor at +1.5R, then ATR trail (ratcheting, floored) after +2R, no cap.
    Conservative: check the adverse extreme vs the stop BEFORE this bar's high
    extends the peak/trail."""
    n = len(h)
    long = direction == "long"
    floor_px = entry + FLOOR_R * risk if long else entry - FLOOR_R * risk
    peak = entry                      # best favorable price so far
    stop = sl_init                    # ratchets toward profit, never loosens
    for j in range(start, n):
        peak_R = (peak - entry) / risk if long else (entry - peak) / risk
        if peak_R >= RUNNER_R:
            cand = (peak - k * atr[j]) if long else (peak + k * atr[j])
            target = max(floor_px, cand) if long else min(floor_px, cand)
        elif peak_R >= FLOOR_R:
            target = floor_px
        else:
            target = sl_init
        stop = max(stop, target) if long else min(stop, target)
        hit = (l[j] <= stop) if long else (h[j] >= stop)
        if hit:
            fill = stop * (1 - SLIP_PCT) if long else stop * (1 + SLIP_PCT)
            return _r(direction, entry, fill, risk)
        peak = max(peak, h[j]) if long else min(peak, l[j])
    return None


def _r(direction, entry, exit_fill, risk_dist):
    gross = (exit_fill - entry) if direction == "long" else (entry - exit_fill)
    fees = FEE_PCT * (entry + exit_fill)
    return (gross - fees) / risk_dist


def report(name, rs):
    if not rs:
        print(f"  {name:<12}: no trades"); return
    wins = [r for r in rs if r > 0]; losses = [r for r in rs if r <= 0]
    pf = (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else float("inf")
    avgwin = (sum(wins) / len(wins)) if wins else 0.0
    print(f"  {name:<12}: {len(rs):>4} trades | win {len(wins)/len(rs):>5.1%} | "
          f"net {sum(rs):+8.1f}R | avg-win {avgwin:+.2f}R | PF {pf:.2f}")


def main():
    n_target = int(sys.argv[1]) if len(sys.argv) > 1 else 50000
    print(f"Fetching ~{n_target} BTCUSDT 15m bars ...", flush=True)
    bars = fetch_klines(n=n_target)
    h = [b["high"] for b in bars]; l = [b["low"] for b in bars]
    c = [b["close"] for b in bars]; v = [b["volume"] for b in bars]; t = [b["time"] for b in bars]
    n = len(c)
    print(f"Got {n} bars: {datetime.fromtimestamp(t[0], timezone.utc):%Y-%m-%d} → "
          f"{datetime.fromtimestamp(t[-1], timezone.utc):%Y-%m-%d}\n", flush=True)

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

    fixed = {"long": [], "short": []}
    runner = {k: {"long": [], "short": []} for k in K_VALUES}
    for i in range(2, n - 1):
        flipUp = macd[i] > macd[i - 1] and macd[i - 1] <= macd[i - 2]
        flipDown = macd[i] < macd[i - 1] and macd[i - 1] >= macd[i - 2]
        volOk = v[i] > vma[i]
        buy = (barssince(isBlue, i) < TREND_LOOK and barssince(grayOrRed, i) < PULL_LOOK
               and c[i] > base[i] and flipUp and macd[i] > 0 and volOk)
        sell = (barssince(isRed, i) < TREND_LOOK and barssince(grayOrBlue, i) < PULL_LOOK
                and c[i] < base[i] and flipDown and macd[i] < 0 and volOk)
        if not (buy or sell):
            continue
        d = "long" if buy else "short"
        try:
            br = compute_bracket(entry_price=c[i], baseline_at_entry=base[i],
                                 recent_low=lowN[i], recent_high=highN[i], direction=d, rr=RR)
        except ValueError:
            continue
        entry = c[i] * (1 + SLIP_PCT) if d == "long" else c[i] * (1 - SLIP_PCT)
        rf = sim_fixed(d, entry, br.stop_loss_price, br.take_profit_price, br.risk_distance, h, l, i + 1)
        if rf is None:
            continue
        fixed[d].append(rf)
        for k in K_VALUES:
            rr_ = sim_runner(d, entry, br.stop_loss_price, br.risk_distance, h, l, atr, i + 1, k)
            if rr_ is not None:
                runner[k][d].append(rr_)

    allfixed = fixed["long"] + fixed["short"]
    print(f"Entries: {len(allfixed)} resolved | RR baseline {RR} | floor {FLOOR_R}R | runner>{RUNNER_R}R | "
          f"fees {FEE_PCT:.2%}/side slip {SLIP_PCT:.2%}/side | CONSERVATIVE 15m intrabar\n")
    print("=== BASELINE: fixed 2R ===")
    report("LONG", fixed["long"]); report("SHORT", fixed["short"]); report("COMBINED", allfixed)
    for k in K_VALUES:
        rk = runner[k]["long"] + runner[k]["short"]
        print(f"\n=== RUNNER: floor 1.5R + ATR*{k} trail (no cap) ===")
        report("LONG", runner[k]["long"]); report("SHORT", runner[k]["short"]); report("COMBINED", rk)
        print(f"  Δ vs fixed (combined): {sum(rk) - sum(allfixed):+.1f}R")


if __name__ == "__main__":
    main()
