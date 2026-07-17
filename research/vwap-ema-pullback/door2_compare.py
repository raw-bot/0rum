"""Porte 2 — gater le pullback au régime du projet, et le comparer à l'AK MACD.

Fair fight: the THREE long-only signals below all run through the SAME exit engine
(liquidation-continuation backtest.py: next-bar-open, ATR bracket 1.5/2.0, fee 5bps +
slip 3bps) on the SAME BTC 4h bars. Only the ENTRY signal differs -> isolates signal
quality, which is the whole question.

  A. pullback RAW               (our signal)
  B. pullback + REGIME GATE     (project's rolling_return_regime, longs blocked when
                                 "unfavorable" — the exact gate AK MACD already uses)
  C. AK MACD confirmed longs    (the incumbent live brain, frozen 4h long-only params)

Run inside the sandbox venv so the real orum brain imports:
  cd .sandbox/0rum-one-shot-home/0rum-trading && \
  uv run python /Applications/0rum/research/vwap-ema-pullback/door2_compare.py
"""
import os
import sys
import numpy as np
import pandas as pd

HERE = "/Applications/0rum/research/vwap-ema-pullback"
SANDBOX = "/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading"
ENGINE = "/Applications/0rum/research/liquidation-continuation/src"
for p in (HERE, SANDBOX, ENGINE):
    sys.path.insert(0, p)

from multi_asset import load_asset_4h  # noqa: E402  (reuses the data_4h zips)
from run import signal_vwap_ema_pullback, EXIT  # noqa: E402
from backtest import run_backtest, compute_metrics, random_baseline, make_arrays  # noqa: E402

# real orum brain + regime classifier
from orum.external.ak_macd import (  # noqa: E402
    AkMacdParams, compute_state,
    _flip_up, _flip_down, _strictly_increasing, _strictly_decreasing,
    _other_long_conditions, _other_short_conditions, _regime_at,
)

REGIME_LOOKBACK = 20      # rolling_return_regime defaults
REGIME_THRESHOLD = 0.003


def confirmed_entries(st, params):
    """Verbatim from scripts/backtest_4h_validation.py (the AK 4h reference) — one-pass
    replay of the candidate machine yielding every confirmed (t, side)."""
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


def regime_unfavorable_mask(df):
    """Vectorised copy of rolling_return_regime's 'unfavorable' label for longs:
    20-bar rolling return = close[t]/close[t-19]-1; unfavorable if <= -0.003."""
    ret = df["close"] / df["close"].shift(REGIME_LOOKBACK - 1) - 1.0
    return (ret <= -REGIME_THRESHOLD).to_numpy()


def fmt(m, beat=None):
    if not m or m.get("n_trades", 0) == 0:
        return "  (no trades)"
    pf = ("%.2f" % m["profit_factor"]) if m.get("profit_factor") else "inf"
    s = (f"n={m['n_trades']:>4}  net={m['net_return_total']:>+6.1%}  PF={pf:>4}  "
         f"win={m['win_rate']:>3.0%}  expR={m['expectancy_r_multiple']:>+6.3f}  maxDD={m['max_drawdown']:>4.0%}")
    if beat is not None:
        s += f"  >rand={beat:>3.0%}"
    return s


def evaluate(name, idx, df, arrays, train_end_ts):
    idx = np.asarray(sorted(idx), dtype=int)
    d = np.ones(len(idx), dtype=int)
    m = compute_metrics(run_backtest(df, idx, d, arrays=arrays, **EXIT))
    beat = None
    if m.get("n_trades", 0) >= 30:
        rnd = random_baseline(df, m["n_trades"], 1.0, EXIT["stop_mult"], EXIT["target_mult"],
                              EXIT["max_hold"], EXIT["fee_bps"], EXIT["slip_bps"], n_sims=120, arrays=arrays)
        beat = float((np.array([r.get("expectancy_r_multiple", 0.0) for r in rnd]) < m["expectancy_r_multiple"]).mean())
    print(f"\n{name}")
    print("  full :", fmt(m, beat))
    if m.get("n_trades", 0) >= 30:
        te = np.asarray(df.index[idx] >= train_end_ts)
        m_oos = compute_metrics(run_backtest(df, idx[te], np.ones(int(te.sum()), dtype=int), arrays=arrays, **EXIT))
        print("  OOS  :", fmt(m_oos))
    return m


def main():
    df = load_asset_4h("BTCUSDT", "futures/um")
    arrays = make_arrays(df)
    train_end_ts = df.index[int(len(df) * 0.70)]
    unfav = regime_unfavorable_mask(df)
    print(f"BTC 4h {df.index[0].date()} -> {df.index[-1].date()}  ({len(df)} bars)  "
          f"regime-unfavorable share: {unfav.mean():.0%}")
    print(f"split OOS at {train_end_ts.date()}")

    for ema_len, vwap_len in [(9, 50), (9, 200)]:
        pidx, _ = signal_vwap_ema_pullback(df, ema_len=ema_len, vwap_len=vwap_len)
        gated = pidx[~unfav[pidx]]
        print(f"\n================ pullback ema{ema_len}/vwap{vwap_len} ================")
        evaluate("A. pullback RAW", pidx, df, arrays, train_end_ts)
        evaluate("B. pullback + REGIME GATE (not-unfavorable)", gated, df, arrays, train_end_ts)

    # C. AK MACD incumbent — frozen 4h long-only params, its own regime filter on.
    candles = [{"ts": int(ts.value // 10**6), "open": o, "high": h, "low": l, "close": c, "volume": v}
               for ts, o, h, l, c, v in zip(df.index, df["open"], df["high"], df["low"], df["close"], df["volume"])]
    params = AkMacdParams(allow_short=False, regime_filter=True)
    st = compute_state(candles, params)
    ak_idx = [t for (t, side) in confirmed_entries(st, params) if side == "long"]
    print("\n================ INCUMBENT ================")
    evaluate("C. AK MACD confirmed longs (live 4h brain, frozen params)", ak_idx, df, arrays, train_end_ts)


if __name__ == "__main__":
    main()
