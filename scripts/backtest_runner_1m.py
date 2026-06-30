"""Lever C — RUNNER exit backtest on TRUE 1m intrabar resolution.

The spec (specs/ak-macd-runner-exit.md) demands 1m data: 15m bars cannot resolve
the ORDER in which a trade's stop/target/trail levels are touched, and the whole
runner thesis is about give-back-vs-capture which is an intrabar-path question.

This script does what neither existing runner script does:
  * ENTRIES come from the 15m production arm-fire brain (backtest_parity.confirmations,
    already proven byte-identical to evaluate_ak_macd_verdict via --verify).
  * EXITS are simulated bar-by-bar on 1m candles from the 15m entry bar's close.
  * Only the EXIT differs between the two engines -> isolates Lever C.

Engines (same entry, same initial SL / risk distance from compute_bracket):
  fixed-2R : live bracket. TP at 2R, SL at initial. (the current behaviour)
  runner-k : once +1.5R touched, floor the stop at +1.5R (never give back below);
             once +2R touched, trail = peak - k*ATR(14, frozen at entry), ratcheting,
             floored at +1.5R, NO upper cap. Exit only when the stop is hit.

Conservative intrabar rule on each 1m bar: the ADVERSE extreme is tested against
the stop BEFORE this bar's favorable extreme extends the peak/trail. So the runner
numbers are a pessimistic LOWER bound (spec guard-rail #2: a peak reached != a peak
captured). If the runner still wins here, the edge is real.

Metrics are in R-space (sizing-independent, matches the spec): net R, expectancy,
win rate, avg-win R, capture efficiency (realized R / MFE on winners), max give-back.
Walk-forward: 5 sequential time folds -> is the runner's net-R edge consistent?

Usage: uv run python scripts/backtest_runner_1m.py [n_15m_bars] [--folds K]
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

import baseline_ak_macd as base          # fetch_klines (15m), constants
import backtest_parity as bp             # confirmations() — proven parity entries

from hermes_trading.external.ak_macd import AkMacdParams, compute_state
from hermes_trading.external.bracket import compute_bracket

RR = 2.0                 # fixed-baseline TP = 2R (live strategy.yaml, runner spec)
FEE_PCT = 0.0004
SLIP_PCT = 0.0003
FLOOR_R = 1.5
RUNNER_R = 2.0
K_VALUES = [2.0, 2.5, 3.0]
TF15_S = 900             # 15m in seconds
CACHE = os.environ.get("HERMES_SCRATCH", "/tmp") + "/btc_1m_cache.json"


# ---------------------------------------------------------------- 1m data (cached)
def fetch_1m(start_s: int, end_s: int) -> tuple[list[int], list[float], list[float]]:
    """Forward-paginate 1m klines covering [start_s, end_s]. Returns (ts_s, high, low).
    Cached to disk keyed by the requested range so reruns are instant."""
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            blob = json.load(f)
        if blob["start"] <= start_s and blob["end"] >= end_s:
            ts = blob["ts"]
            lo_i = next(i for i, t in enumerate(ts) if t >= start_s)
            hi_i = len(ts) - next(i for i, t in enumerate(reversed(ts)) if t <= end_s)
            print(f"  (1m cache hit: {len(ts)} bars)", flush=True)
            return ts[lo_i:hi_i], blob["h"][lo_i:hi_i], blob["l"][lo_i:hi_i]

    ts: list[int] = []; hh: list[float] = []; ll: list[float] = []
    cursor = start_s * 1000
    end_ms = end_s * 1000
    n_calls = 0
    while cursor <= end_ms:
        url = (f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m"
               f"&limit=1000&startTime={cursor}")
        req = urllib.request.Request(url, headers={"User-Agent": "hermes-backtest"})
        with urllib.request.urlopen(req, timeout=30) as r:
            batch = json.loads(r.read().decode())
        if not batch:
            break
        for k in batch:
            ts.append(k[0] // 1000); hh.append(float(k[2])); ll.append(float(k[3]))
        cursor = batch[-1][0] + 60_000
        n_calls += 1
        if n_calls % 50 == 0:
            print(f"    1m fetch: {len(ts)} bars ...", flush=True)
        time.sleep(0.2)
        if len(batch) < 1000:
            break
    with open(CACHE, "w") as f:
        json.dump({"start": ts[0], "end": ts[-1], "ts": ts, "h": hh, "l": ll}, f)
    print(f"  (1m fetched + cached: {len(ts)} bars)", flush=True)
    return ts, hh, ll


def atr15(bars: list[dict], n: int = 14) -> list[float]:
    h = [b["high"] for b in bars]; l = [b["low"] for b in bars]; c = [b["close"] for b in bars]
    tr = [h[0] - l[0]]
    for i in range(1, len(h)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    a = 1 / n; out = [tr[0]]
    for x in tr[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


# ------------------------------------------------------------------- exit engines
def _net_r(direction: str, entry: float, exit_fill: float, risk: float) -> float:
    gross = (exit_fill - entry) if direction == "long" else (entry - exit_fill)
    return (gross - FEE_PCT * (entry + exit_fill)) / risk


def natural_mfe(d, entry, sl_init, risk, H, L, start):
    """The uncapped max-favorable-excursion (in R) the setup actually offered before
    the INITIAL structural stop was hit — the honest 'how much was on the table',
    independent of which exit engine is used. Adverse-before-peak (conservative)."""
    long = d == "long"; peak = entry
    for j in range(start, len(H)):
        if (L[j] <= sl_init) if long else (H[j] >= sl_init):
            break
        peak = max(peak, H[j]) if long else min(peak, L[j])
    return (peak - entry) / risk if long else (entry - peak) / risk


def sim_fixed(d, entry, sl, tp, risk, H, L, start):
    """Returns (exit_R, mfe_R) or None. SL-first conservative within each 1m bar."""
    long = d == "long"; mfe = 0.0
    for j in range(start, len(H)):
        adverse = (L[j] <= sl) if long else (H[j] >= sl)
        if adverse:
            fill = sl * (1 - SLIP_PCT) if long else sl * (1 + SLIP_PCT)
            return _net_r(d, entry, fill, risk), mfe
        fav = (H[j] >= tp) if long else (L[j] <= tp)
        if fav:
            r = _net_r(d, entry, tp, risk)
            return r, max(mfe, r)
        cur = (H[j] - entry) / risk if long else (entry - L[j]) / risk
        mfe = max(mfe, cur)
    return None


def sim_runner(d, entry, sl_init, risk, H, L, atr, start, k):
    """Returns (exit_R, mfe_R) or None. Floor 1.5R after touch, ATR*k trail (frozen
    ATR) after 2R, ratcheting, floored, no cap. Conservative: adverse-before-peak."""
    long = d == "long"
    floor_px = entry + FLOOR_R * risk if long else entry - FLOOR_R * risk
    peak = entry; stop = sl_init; mfe = 0.0
    for j in range(start, len(H)):
        peak_R = (peak - entry) / risk if long else (entry - peak) / risk
        if peak_R >= RUNNER_R:
            cand = (peak - k * atr) if long else (peak + k * atr)
            target = max(floor_px, cand) if long else min(floor_px, cand)
        elif peak_R >= FLOOR_R:
            target = floor_px
        else:
            target = sl_init
        stop = max(stop, target) if long else min(stop, target)
        adverse = (L[j] <= stop) if long else (H[j] >= stop)
        if adverse:
            fill = stop * (1 - SLIP_PCT) if long else stop * (1 + SLIP_PCT)
            return _net_r(d, entry, fill, risk), mfe
        peak = max(peak, H[j]) if long else min(peak, L[j])
        cur = (peak - entry) / risk if long else (entry - peak) / risk
        mfe = max(mfe, cur)
    return None


# ------------------------------------------------------------------------ metrics
def stats(trades: list[dict]):
    """trades: list of {r, mfe, dir}. Returns a metrics dict."""
    if not trades:
        return None
    rs = [t["r"] for t in trades]
    wins = [t for t in trades if t["r"] > 0]
    losses = [t for t in trades if t["r"] <= 0]
    gl = -sum(t["r"] for t in losses)
    pf = (sum(t["r"] for t in wins) / gl) if gl > 0 else float("inf")
    # capture vs the NATURAL (uncapped) MFE the setup offered — comparable across engines
    cap = [t["r"] / t["nat"] for t in wins if t["nat"] > 0]
    giveback = [t["nat"] - t["r"] for t in trades if t["nat"] > 0]
    avail = [t["nat"] for t in wins if t["nat"] > 0]
    return {
        "n": len(trades), "wr": len(wins) / len(trades),
        "net_r": sum(rs), "exp_r": sum(rs) / len(trades),
        "avg_win": (sum(t["r"] for t in wins) / len(wins)) if wins else 0.0,
        "pf": pf,
        "capture": (sum(cap) / len(cap)) if cap else 0.0,
        "avg_avail": (sum(avail) / len(avail)) if avail else 0.0,
        "max_giveback": max(giveback) if giveback else 0.0,
    }


def show(name, m):
    if not m:
        print(f"  {name:<16}: no trades"); return
    print(f"  {name:<16}: {m['n']:>4} tr | WR {m['wr']:>5.1%} | net {m['net_r']:+7.1f}R | "
          f"exp {m['exp_r']:+.3f}R | avg-win {m['avg_win']:+.2f}R | PF {m['pf']:.2f} | "
          f"avail {m['avg_avail']:.1f}R | capt {m['capture']:.0%} | maxGB {m['max_giveback']:.1f}R")


# --------------------------------------------------------------------------- main
def run_engines(entries, bars15, A15, T1, H1, L1):
    """For every (i, d) 15m entry, simulate fixed + each runner-k on 1m. Returns
    {engine: [trade dicts]}. Each trade carries its 15m entry time for fold splitting."""
    sw = AkMacdParams().swing_look
    st = compute_state(bars15, AkMacdParams())
    c15 = st.closes; h15 = st.highs; l15 = st.lows
    t15 = [b["time"] for b in bars15]
    out = {"fixed": [], **{f"runner{k}": [] for k in K_VALUES}}
    p1 = 0
    for (i, d) in entries:
        try:
            br = compute_bracket(entry_price=c15[i], baseline_at_entry=st.baseline[i],
                                 recent_low=min(l15[max(0, i - sw + 1): i + 1]),
                                 recent_high=max(h15[max(0, i - sw + 1): i + 1]),
                                 direction=d, rr=RR)
        except ValueError:
            continue
        entry = c15[i] * (1 + SLIP_PCT) if d == "long" else c15[i] * (1 - SLIP_PCT)
        open_at = t15[i] + TF15_S                       # position opens at bar close
        while p1 < len(T1) and T1[p1] < open_at:
            p1 += 1
        start = p1
        if start >= len(T1):
            break
        rf = sim_fixed(d, entry, br.stop_loss_price, br.take_profit_price, br.risk_distance,
                       H1, L1, start)
        if rf is None:
            continue
        nat = natural_mfe(d, entry, br.stop_loss_price, br.risk_distance, H1, L1, start)
        out["fixed"].append({"r": rf[0], "nat": nat, "dir": d, "t": t15[i]})
        for k in K_VALUES:
            rr_ = sim_runner(d, entry, br.stop_loss_price, br.risk_distance, H1, L1,
                             A15[i], start, k)
            if rr_ is not None:
                out[f"runner{k}"].append({"r": rr_[0], "nat": nat, "dir": d, "t": t15[i]})
    return out


def main():
    argv = sys.argv[1:]
    folds = 5
    n_target = 50000
    for k, a in enumerate(argv):
        if a == "--folds":
            folds = int(argv[k + 1])
        elif a.isdigit() and (k == 0 or argv[k - 1] != "--folds"):
            n_target = int(a)

    params = AkMacdParams()
    print(f"Fetching ~{n_target} BTCUSDT 15m bars ...", flush=True)
    bars15 = base.fetch_klines(n=n_target)
    t15 = [b["time"] for b in bars15]
    span = (datetime.fromtimestamp(t15[0], timezone.utc),
            datetime.fromtimestamp(t15[-1], timezone.utc))
    print(f"Got {len(bars15)} 15m bars: {span[0]:%Y-%m-%d} → {span[1]:%Y-%m-%d}", flush=True)

    entries = bp.confirmations(bars15, params)
    print(f"Arm-fire entries (15m): {len(entries)}", flush=True)
    A15 = atr15(bars15, params.atr_len)

    print("Fetching 1m intrabar series (cached) ...", flush=True)
    T1, H1, L1 = fetch_1m(t15[0], t15[-1] + TF15_S)
    print(f"1m bars: {len(T1)}\n", flush=True)

    res = run_engines(entries, bars15, A15, T1, H1, L1)

    print("=" * 100)
    print(f"FULL SAMPLE — arm-fire entries, 1m intrabar exits  ({span[0]:%Y-%m-%d} → {span[1]:%Y-%m-%d})")
    print("=" * 100)
    fixed_m = stats(res["fixed"])
    show("fixed-2R", fixed_m)
    best_k = None; best_net = fixed_m["net_r"] if fixed_m else -1e9
    for k in K_VALUES:
        m = stats(res[f"runner{k}"])
        show(f"runner k={k}", m)
        if m:
            print(f"  {'':<16}  Δ net vs fixed: {m['net_r'] - fixed_m['net_r']:+.1f}R  "
                  f"({'PROFITABLE' if m['net_r'] > 0 else 'still losing'})")
            if m["net_r"] > best_net:
                best_net, best_k = m["net_r"], k

    print("\n" + "=" * 100)
    print(f"WALK-FORWARD — {folds} sequential time folds (consistency of the runner edge)")
    print("=" * 100)
    # split by 15m entry time into `folds` equal spans
    t0, t1 = t15[0], t15[-1]
    width = (t1 - t0) / folds
    for fi in range(folds):
        lo = t0 + fi * width; hi = t0 + (fi + 1) * width
        fl = {e: [t for t in res[e] if lo <= t["t"] < hi] for e in res}
        fm = stats(fl["fixed"])
        print(f"\nFold {fi+1}/{folds}  {datetime.fromtimestamp(lo, timezone.utc):%Y-%m-%d} → "
              f"{datetime.fromtimestamp(hi, timezone.utc):%Y-%m-%d}")
        show("  fixed-2R", fm)
        for k in K_VALUES:
            m = stats(fl[f"runner{k}"])
            if m and fm:
                tag = "✓" if m["net_r"] > fm["net_r"] else "✗"
                print(f"    runner k={k:<4}: net {m['net_r']:+6.1f}R  (Δ {m['net_r']-fm['net_r']:+.1f}R) {tag}")

    print("\n" + "=" * 100)
    if best_k is not None and best_net > 0:
        print(f"VERDICT: best runner k={best_k} net {best_net:+.1f}R > 0 AND beats fixed. "
              f"Check walk-forward consistency above before trusting it.")
    elif best_k is not None:
        print(f"VERDICT: runner k={best_k} beats fixed (Δ net) but is still net-negative "
              f"({best_net:+.1f}R). Better exit, no edge.")
    else:
        print(f"VERDICT: no runner variant beats fixed-2R. The runner thesis is dead on this data.")
    print("=" * 100)


if __name__ == "__main__":
    main()
