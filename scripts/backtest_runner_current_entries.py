"""Stage-1.5 backtest: CURRENT live entries (ak_macd.py brain: confirmation window
+ regime + candle-direction gate) x {fixed-2R, runner exit}.

Generates entries with the EXACT live brain (compute_state + the candidate machine,
including require_candle_direction), then applies both exit engines to each. Tells us
whether the strategy AS IT RUNS LIVE crosses PF 1.0. Conservative 15m intrabar.

Usage: uv run python scripts/backtest_runner_current_entries.py [n_bars]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone

from hermes_trading.external.ak_macd import (
    AkMacdParams, compute_state,
    _flip_up, _flip_down, _strictly_increasing, _strictly_decreasing,
    _other_long_conditions, _other_short_conditions, _regime_at,
)
from hermes_trading.external.bracket import compute_bracket

RR = 2.0
FEE_PCT = 0.0004
SLIP_PCT = 0.0003
FLOOR_R = 1.5
RUNNER_R = 2.0
K_VALUES = [2.0, 2.5, 3.0]


def fetch_klines(symbol="BTCUSDT", interval="15m", n=50000):
    out = []
    end = int(time.time() * 1000)
    while len(out) < n:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
               f"&interval={interval}&limit=1000&endTime={end}")
        req = urllib.request.Request(url, headers={"User-Agent": "hermes-backtest"})
        with urllib.request.urlopen(req, timeout=30) as r:
            batch = json.loads(r.read().decode())
        if not batch:
            break
        out = [{"ts": k[0], "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                "close": float(k[4]), "volume": float(k[5])} for k in batch] + out
        end = batch[0][0] - 1
        time.sleep(0.25)
        if len(batch) < 1000:
            break
    return out[-n:]


def rma(xs, n):
    a = 1 / n; out = [xs[0]]
    for x in xs[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def atr_series(h, l, c, n=14):
    tr = [h[0] - l[0]]
    for i in range(1, len(h)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    return rma(tr, n)


def _r(direction, entry, exit_fill, risk):
    gross = (exit_fill - entry) if direction == "long" else (entry - exit_fill)
    return (gross - FEE_PCT * (entry + exit_fill)) / risk


def sim_fixed(d, entry, sl, tp, risk, h, l, start):
    for j in range(start, len(h)):
        if (l[j] <= sl) if d == "long" else (h[j] >= sl):
            return _r(d, entry, sl * (1 - SLIP_PCT) if d == "long" else sl * (1 + SLIP_PCT), risk)
        if (h[j] >= tp) if d == "long" else (l[j] <= tp):
            return _r(d, entry, tp, risk)
    return None


def sim_runner(d, entry, sl_init, risk, h, l, atr, start, k):
    long = d == "long"
    floor_px = entry + FLOOR_R * risk if long else entry - FLOOR_R * risk
    peak = entry; stop = sl_init
    for j in range(start, len(h)):
        peak_R = (peak - entry) / risk if long else (entry - peak) / risk
        if peak_R >= RUNNER_R:
            cand = (peak - k * atr[j]) if long else (peak + k * atr[j])
            target = max(floor_px, cand) if long else min(floor_px, cand)
        elif peak_R >= FLOOR_R:
            target = floor_px
        else:
            target = sl_init
        stop = max(stop, target) if long else min(stop, target)
        if (l[j] <= stop) if long else (h[j] >= stop):
            return _r(d, entry, stop * (1 - SLIP_PCT) if long else stop * (1 + SLIP_PCT), risk)
        peak = max(peak, h[j]) if long else min(peak, l[j])
    return None


def confirmed_entries(st, params):
    """Replay the live candidate machine, collecting every CONFIRMED entry (t, side).
    Mirrors run_candidate_machine but yields all confirmations instead of the last."""
    n = len(st.macd); W = params.candidate_window_bars; cb = params.confirmation_bars
    cand = None
    for t in range(2, n):
        fu = _flip_up(st.macd, t); fd = _flip_down(st.macd, t)
        reg = _regime_at(st.closes, t)[0] if params.regime_filter else None
        if fu:
            cand = (t, "long")
        elif fd and params.allow_short:
            cand = (t, "short")
        elif cand is not None:
            c0, side = cand
            if t > c0 + W:
                cand = None
            elif side == "long":
                if not (st.macd[t] > st.macd[t - 1]):
                    cand = None
                elif _strictly_increasing(st.macd, t, cb) and _other_long_conditions(st, t, params) \
                        and not (params.regime_filter and reg == "unfavorable"):
                    yield (t, "long"); cand = None
                elif not _strictly_increasing(st.macd, t, cb):
                    pass  # keep waiting in window
            else:
                if not (st.macd[t] < st.macd[t - 1]):
                    cand = None
                elif _strictly_decreasing(st.macd, t, cb) and _other_short_conditions(st, t, params) \
                        and not (params.regime_filter and reg == "favorable"):
                    yield (t, "short"); cand = None


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
    interval = sys.argv[2] if len(sys.argv) > 2 else "15m"
    print(f"Fetching ~{n_target} BTCUSDT {interval} bars ...", flush=True)
    bars = fetch_klines(interval=interval, n=n_target)
    h = [b["high"] for b in bars]; l = [b["low"] for b in bars]; c = [b["close"] for b in bars]
    t = [b["ts"] // 1000 for b in bars]
    print(f"Got {len(c)} bars: {datetime.fromtimestamp(t[0], timezone.utc):%Y-%m-%d} → "
          f"{datetime.fromtimestamp(t[-1], timezone.utc):%Y-%m-%d}", flush=True)

    params = AkMacdParams()  # live defaults: confirm=2, window=2, regime on, candle gate on
    print(f"Entry brain = LIVE (confirm {params.confirmation_bars}, window {params.candidate_window_bars}, "
          f"regime {params.regime_filter}, candle_gate {params.require_candle_direction})", flush=True)
    st = compute_state(bars, params)
    atr = atr_series(h, l, c, params.atr_len)

    fixed = {"long": [], "short": []}
    runner = {k: {"long": [], "short": []} for k in K_VALUES}
    for (i, d) in confirmed_entries(st, params):
        sw = params.swing_look
        try:
            br = compute_bracket(entry_price=c[i], baseline_at_entry=st.baseline[i],
                                 recent_low=min(l[max(0, i - sw + 1): i + 1]),
                                 recent_high=max(h[max(0, i - sw + 1): i + 1]),
                                 direction=d, rr=RR)
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
    print(f"\nEntries: {len(allfixed)} resolved | RR {RR} | floor {FLOOR_R}R | runner>{RUNNER_R}R | "
          f"CONSERVATIVE 15m intrabar\n")
    print("=== BASELINE: fixed 2R (current entries) ===")
    report("LONG", fixed["long"]); report("SHORT", fixed["short"]); report("COMBINED", allfixed)
    for k in K_VALUES:
        rk = runner[k]["long"] + runner[k]["short"]
        print(f"\n=== RUNNER floor1.5R + ATR*{k} (current entries) ===")
        report("LONG", runner[k]["long"]); report("SHORT", runner[k]["short"]); report("COMBINED", rk)
        print(f"  Δ vs fixed: {sum(rk) - sum(allfixed):+.1f}R  | PF {'>1 PROFITABLE' if sum(rk) > 0 else '<1 still losing'}")


if __name__ == "__main__":
    main()
