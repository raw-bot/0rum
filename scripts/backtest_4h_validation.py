"""Out-of-sample validation of the 4h AK MACD edge (current live brain).

Same entries (compute_state + candidate machine incl. candle gate), exits = fixed-2R
and runner (floor 1.5R + ATR*K trail, K fixed a-priori). Reports:
  - in-sample (<= SPLIT) vs out-of-sample (> SPLIT) train/test check
  - per-year breakdown (is the edge consistent or one-bull-run?)
  - COMBINED and LONG-ONLY for each
Conservative intrabar (pessimistic lower bound). No parameter fitting on the test set.

Usage: uv run python scripts/backtest_4h_validation.py [n_bars] [interval] [split_year]
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

RR, FEE_PCT, SLIP_PCT = 2.0, 0.0004, 0.0003
FLOOR_R, RUNNER_R, K = 1.5, 2.0, 2.5   # K fixed a-priori (median, not the best-on-full-window)


def fetch_klines(symbol="BTCUSDT", interval="4h", n=17000):
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


def _r(d, entry, fill, risk):
    gross = (fill - entry) if d == "long" else (entry - fill)
    return (gross - FEE_PCT * (entry + fill)) / risk


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
    peak, stop = entry, sl_init
    for j in range(start, len(h)):
        pr = (peak - entry) / risk if long else (entry - peak) / risk
        if pr >= RUNNER_R:
            cand = (peak - k * atr[j]) if long else (peak + k * atr[j])
            target = max(floor_px, cand) if long else min(floor_px, cand)
        elif pr >= FLOOR_R:
            target = floor_px
        else:
            target = sl_init
        stop = max(stop, target) if long else min(stop, target)
        if (l[j] <= stop) if long else (h[j] >= stop):
            return _r(d, entry, stop * (1 - SLIP_PCT) if long else stop * (1 + SLIP_PCT), risk)
        peak = max(peak, h[j]) if long else min(peak, l[j])
    return None


def confirmed_entries(st, params):
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
            else:
                if not (st.macd[t] < st.macd[t - 1]):
                    cand = None
                elif _strictly_decreasing(st.macd, t, cb) and _other_short_conditions(st, t, params) \
                        and not (params.regime_filter and reg == "favorable"):
                    yield (t, "short"); cand = None


def stats(trades, key, dirs):
    g = [t for t in trades if t["dir"] in dirs]
    rs = [t[key] for t in g]
    if not rs:
        return "   no trades"
    wins = [r for r in rs if r > 0]; losses = [r for r in rs if r <= 0]
    pf = (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else float("inf")
    return f"{len(rs):>4} tr | win {len(wins)/len(rs):>5.1%} | net {sum(rs):+7.1f}R | PF {pf:.2f}"


def block(title, trades):
    print(f"\n{title}  ({len(trades)} trades)")
    print(f"  COMBINED  fixed-2R : {stats(trades, 'rf', ('long','short'))}")
    print(f"  COMBINED  runner   : {stats(trades, 'rr', ('long','short'))}")
    print(f"  LONG-ONLY fixed-2R : {stats(trades, 'rf', ('long',))}")
    print(f"  LONG-ONLY runner   : {stats(trades, 'rr', ('long',))}")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 17000
    interval = sys.argv[2] if len(sys.argv) > 2 else "4h"
    split_year = int(sys.argv[3]) if len(sys.argv) > 3 else 2024
    print(f"Fetching ~{n} BTCUSDT {interval} bars ...", flush=True)
    bars = fetch_klines(interval=interval, n=n)
    h = [b["high"] for b in bars]; l = [b["low"] for b in bars]; c = [b["close"] for b in bars]
    ts = [b["ts"] // 1000 for b in bars]
    print(f"Got {len(c)} bars: {datetime.fromtimestamp(ts[0], timezone.utc):%Y-%m-%d} → "
          f"{datetime.fromtimestamp(ts[-1], timezone.utc):%Y-%m-%d} | runner K={K} (fixed a-priori)", flush=True)
    params = AkMacdParams()
    st = compute_state(bars, params)
    atr = atr_series(h, l, c, params.atr_len)

    trades = []
    for (i, d) in confirmed_entries(st, params):
        sw = params.swing_look
        try:
            br = compute_bracket(entry_price=c[i], baseline_at_entry=st.baseline[i],
                                 recent_low=min(l[max(0, i - sw + 1): i + 1]),
                                 recent_high=max(h[max(0, i - sw + 1): i + 1]), direction=d, rr=RR)
        except ValueError:
            continue
        entry = c[i] * (1 + SLIP_PCT) if d == "long" else c[i] * (1 - SLIP_PCT)
        rf = sim_fixed(d, entry, br.stop_loss_price, br.take_profit_price, br.risk_distance, h, l, i + 1)
        if rf is None:
            continue
        rr_ = sim_runner(d, entry, br.stop_loss_price, br.risk_distance, h, l, atr, i + 1, K)
        if rr_ is None:
            continue
        trades.append({"year": datetime.fromtimestamp(ts[i], timezone.utc).year, "dir": d, "rf": rf, "rr": rr_})

    block("=== FULL WINDOW ===", trades)
    block(f"=== IN-SAMPLE (< {split_year}) ===", [t for t in trades if t["year"] < split_year])
    block(f"=== OUT-OF-SAMPLE (>= {split_year}) ===", [t for t in trades if t["year"] >= split_year])
    print("\n=== PER YEAR (LONG-ONLY runner) — is the edge consistent? ===")
    for y in sorted({t["year"] for t in trades}):
        print(f"  {y}: {stats([t for t in trades if t['year'] == y], 'rr', ('long',))}")


if __name__ == "__main__":
    main()
