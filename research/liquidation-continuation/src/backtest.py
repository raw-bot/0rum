"""Event-driven backtest engine: next-bar-open entry, ATR bracket exit (stop/target),
timeout exit, fees + slippage, single-position-at-a-time, full trade ledger.

No-lookahead guarantee: a signal computed using bar i's close (and any data
known as of bar i's close) is executed at bar i+1's OPEN. Stop/target levels
are derived from data known at signal time (ATR at bar i). Forward scanning
for stop/target/timeout only uses bars >= i+1, which is the trade's own future
-- standard backtest mechanics, not lookahead into the signal itself.
"""
import numpy as np
import pandas as pd


def make_arrays(df):
    return {
        "ts": df.index.values,
        "open": df["open"].to_numpy(dtype=float),
        "high": df["high"].to_numpy(dtype=float),
        "low": df["low"].to_numpy(dtype=float),
        "close": df["close"].to_numpy(dtype=float),
        "atr_pct": df["atr_pct"].to_numpy(dtype=float),
    }


def run_backtest(df, signal_idx, direction, stop_mult=1.5, target_mult=2.0, max_hold=96,
                  fee_bps=4.0, slip_bps=2.0, allow_overlap=False, arrays=None):
    """signal_idx: sorted array of integer positions in df where a signal fires
    (signal known as of close of that bar). direction: same-length array of +1/-1.
    Returns a DataFrame of trades."""
    a = arrays or make_arrays(df)
    n = len(a["close"])
    trades = []
    next_free_idx = 0  # earliest bar index at which a new position may open (no-overlap mode)

    for k in range(len(signal_idx)):
        i = int(signal_idx[k])
        d = int(direction[k])
        entry_i = i + 1
        if entry_i >= n:
            continue
        if allow_overlap is False and entry_i < next_free_idx:
            continue  # would overlap with currently open position
        if np.isnan(a["atr_pct"][i]) or a["atr_pct"][i] <= 0:
            continue

        raw_entry = a["open"][entry_i]
        slip = slip_bps / 10000.0
        entry_price = raw_entry * (1 + d * slip)  # worse fill in trade direction
        atr_pct = a["atr_pct"][i]
        stop_dist = stop_mult * atr_pct
        target_dist = target_mult * atr_pct
        stop_price = entry_price * (1 - d * stop_dist)
        target_price = entry_price * (1 + d * target_dist)

        exit_price = None
        exit_reason = None
        exit_j = None
        last_j = min(entry_i + max_hold - 1, n - 1)
        for j in range(entry_i, last_j + 1):
            hi, lo = a["high"][j], a["low"][j]
            stop_hit = (lo <= stop_price) if d == 1 else (hi >= stop_price)
            target_hit = (hi >= target_price) if d == 1 else (lo <= target_price)
            if stop_hit and target_hit:
                exit_price, exit_reason, exit_j = stop_price, "stop", j  # conservative: stop wins ties
                break
            elif stop_hit:
                exit_price, exit_reason, exit_j = stop_price, "stop", j
                break
            elif target_hit:
                exit_price, exit_reason, exit_j = target_price, "target", j
                break
        if exit_price is None:
            exit_j = last_j
            exit_price = a["close"][last_j]
            exit_reason = "timeout"
        exit_price = exit_price * (1 - d * slip)  # worse fill on exit too

        gross_ret = d * (exit_price - entry_price) / entry_price
        fee_cost = 2 * fee_bps / 10000.0  # round-trip taker fee
        net_ret = gross_ret - fee_cost
        r_multiple = net_ret / stop_dist  # post-fee return expressed in units of planned risk

        trades.append({
            "entry_ts": a["ts"][entry_i], "exit_ts": a["ts"][exit_j], "signal_ts": a["ts"][i],
            "direction": d, "entry_price": entry_price, "exit_price": exit_price,
            "bars_held": exit_j - entry_i + 1, "exit_reason": exit_reason,
            "gross_ret": gross_ret, "net_ret": net_ret, "r_multiple": r_multiple,
        })
        next_free_idx = exit_j + 1

    return pd.DataFrame(trades)


def compute_metrics(trades, risk_per_trade=0.01):
    """Equity is built from R-multiples at a fixed fractional risk per trade
    (default 1% of equity risked per trade, single position at a time -- this
    is standard fixed-fractional position sizing, not the unrealistic
    100%-of-equity-per-trade compounding you'd get from raw price returns,
    which mechanically wipes out any strategy with thousands of trades and a
    even a tiny negative edge regardless of whether the edge is real)."""
    if trades is None or len(trades) == 0:
        return {"n_trades": 0}
    r = trades["net_ret"].to_numpy()
    r_mult = trades["r_multiple"].to_numpy()
    equity = np.cumprod(1 + risk_per_trade * r_mult)
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1
    max_dd = dd.min()
    wins = r[r > 0]
    losses = r[r <= 0]
    profit_factor = (wins.sum() / -losses.sum()) if losses.sum() < 0 else np.inf
    win_rate = (r > 0).mean()
    expectancy = r.mean()
    expectancy_r = r_mult.mean()
    net_return_total = equity[-1] - 1

    long_r = trades.loc[trades["direction"] == 1, "net_ret"]
    short_r = trades.loc[trades["direction"] == -1, "net_ret"]
    long_rm = trades.loc[trades["direction"] == 1, "r_multiple"]
    short_rm = trades.loc[trades["direction"] == -1, "r_multiple"]

    return {
        "n_trades": int(len(trades)),
        "net_return_total": float(net_return_total),
        "max_drawdown": float(max_dd),
        "profit_factor": float(profit_factor) if np.isfinite(profit_factor) else None,
        "win_rate": float(win_rate),
        "expectancy_per_trade": float(expectancy),
        "expectancy_r_multiple": float(expectancy_r),
        "risk_per_trade_pct": float(risk_per_trade),
        "avg_win": float(wins.mean()) if len(wins) else None,
        "avg_loss": float(losses.mean()) if len(losses) else None,
        "n_long": int((trades["direction"] == 1).sum()),
        "n_short": int((trades["direction"] == -1).sum()),
        "long_net_return_total": float(np.cumprod(1 + risk_per_trade * long_rm.to_numpy())[-1] - 1) if len(long_r) else None,
        "short_net_return_total": float(np.cumprod(1 + risk_per_trade * short_rm.to_numpy())[-1] - 1) if len(short_r) else None,
        "long_win_rate": float((long_r > 0).mean()) if len(long_r) else None,
        "short_win_rate": float((short_r > 0).mean()) if len(short_r) else None,
        "avg_bars_held": float(trades["bars_held"].mean()),
        "exit_reason_counts": trades["exit_reason"].value_counts().to_dict(),
    }


def outlier_dominance_check(trades, n_drop=2):
    """Drop the n_drop best trades; report whether PF and total return survive."""
    if len(trades) <= n_drop:
        return {"insufficient_trades": True}
    sorted_t = trades.sort_values("net_ret", ascending=False)
    trimmed = sorted_t.iloc[n_drop:]
    m = compute_metrics(trimmed)
    return {
        "pf_after_dropping_top_%d" % n_drop: m.get("profit_factor"),
        "return_after_dropping_top_%d" % n_drop: m.get("net_return_total"),
        "outlier_dominated": (m.get("profit_factor") or 0) < 1.0 or (m.get("net_return_total") or -1) < 0,
    }


def random_baseline(df, n_trades, long_frac, stop_mult, target_mult, max_hold, fee_bps, slip_bps,
                     n_sims=200, seed=42, arrays=None, valid_idx=None):
    """Same exit rule / costs, but entries chosen uniformly at random (matching the
    strategy's long/short mix and trade count). Isolates whether SIGNAL TIMING adds
    value beyond 'trading this exit rule in this market regime'."""
    a = arrays or make_arrays(df)
    n = len(a["close"])
    if valid_idx is None:
        valid_idx = np.where(~np.isnan(a["atr_pct"]) & (a["atr_pct"] > 0))[0]
        valid_idx = valid_idx[valid_idx < n - 1]
    rng = np.random.default_rng(seed)
    n_long = int(round(n_trades * long_frac))
    n_short = n_trades - n_long
    results = []
    for s in range(n_sims):
        idx = rng.choice(valid_idx, size=n_trades, replace=False)
        idx.sort()
        directions = np.array([1] * n_long + [-1] * n_short)
        rng.shuffle(directions)
        tr = run_backtest(df, idx, directions, stop_mult=stop_mult, target_mult=target_mult,
                           max_hold=max_hold, fee_bps=fee_bps, slip_bps=slip_bps,
                           allow_overlap=True, arrays=a)
        m = compute_metrics(tr)
        results.append(m)
    return results


def chronological_split(df, train_frac=0.7):
    n = len(df)
    cut = int(n * train_frac)
    return df.index[cut]
