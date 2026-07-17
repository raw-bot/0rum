"""Shadow regime detector — E[R | regime detected EX ANTE], never a posteriori.

V1 uses the two ablation-validated trend features, both computed ONLY from
candles closed at the trade's entry timestamp (SnapshotProvider.as_of is the
lookahead guard):

  channel_mid_slope_atr   slope of the EMA20 high/low channel midpoint over 5
                          bars, normalised by ATR14 (same definition as
                          features.candidate_features);
  efficiency_ratio_10     Kaufman ER over 10 bars.

Regime is measured on the 4h series of the trade's symbol for every strategy
(the motivating failure — HA's EMA-slope gate letting longs through in
bear-market bounces — is a 4h-regime failure; a uniform market-regime clock
also keeps the filter strategy-agnostic in its inputs).

Walk-forward protocol (STRICT):
  * calendar windows: train `train_months`, evaluate the following
    `eval_months`, rolled forward by `eval_months`;
  * thresholds are learned on the train trades of a window, FROZEN, and
    applied verbatim to the trades of the following eval window;
  * a trade is only ever scored by thresholds learned on data that predates
    its entire training window; trades never covered by an eval window are
    tagged `train_only` and excluded from OOS statistics.

Learning is a deterministic grid search over train-quantile thresholds of
each feature (including "no filter"): pick the pair maximising the mean net R
of passing trades subject to keeping at least `min_keep_fraction` of the
train trades (and >= `min_keep_trades`); ties prefer keeping more trades,
then lower thresholds. Simple on purpose — the study measures whether ANY
honestly-learned static threshold carries information out of sample.

Output: one shadow verdict per trade ("would have allowed/blocked, why") and
per-strategy OOS aggregates: conditional expectancy (allowed vs blocked),
trades kept, forgone profits / avoided losses, and a counterfactual
balance-walk drawdown (fixed, non-compounding sizes — documented
approximation).

This module NEVER touches live signals or state/ — it reads finished replay
ledgers and snapshot data only.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.replay_harness.features import _atr, efficiency_ratio
from scripts.replay_harness.timeline import SnapshotProvider

FEATURE_WINDOW_BARS = 60  # enough closed 4h bars for EMA20 warmup + slope
QUANTILE_GRID = (None, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7)  # None = no filter


def _ms(date_str: str) -> int:
    return int(datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc).timestamp() * 1000)


def _iso_to_ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def _add_months(date_str: str, months: int) -> str:
    d = datetime.fromisoformat(date_str)
    month = d.month - 1 + months
    return d.replace(year=d.year + month // 12, month=month % 12 + 1).strftime("%Y-%m-%d")


def regime_features(candles: list[dict], *, ema_len: int = 20, slope_len: int = 5) -> dict:
    """The two v1 regime features from a window of CLOSED candles."""
    from orum.strategies.ha_trend import _ema
    if len(candles) < ema_len + slope_len + 1:
        return {"channel_mid_slope_atr": float("nan"), "efficiency_ratio_10": float("nan")}
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    atr = _atr(candles)
    ema_high = _ema(highs, ema_len)
    ema_low = _ema(lows, ema_len)
    mid_now = (ema_high[-1] + ema_low[-1]) / 2.0
    mid_prev = (ema_high[-1 - slope_len] + ema_low[-1 - slope_len]) / 2.0
    return {
        "channel_mid_slope_atr": (mid_now - mid_prev) / atr if atr > 0 else float("nan"),
        "efficiency_ratio_10": efficiency_ratio(closes, 10),
    }


def load_trades(run_dir: Path) -> list[dict]:
    """Completed trades from a runtime ledger, with net R (fees included)."""
    fills_path = Path(run_dir) / "runtime_ledger" / "fills.jsonl"
    fills = [json.loads(line) for line in fills_path.read_text().splitlines() if line.strip()]
    open_by_key: dict[str, dict] = {}
    trades: list[dict] = []
    for fill in fills:
        key = fill["strategy_id"]
        if fill["action"] == "open":
            open_by_key[key] = fill
        elif fill["action"] == "close" and key in open_by_key:
            entry = open_by_key.pop(key)
            risk_usd = entry["qty"] * entry["atr_risk"]
            net = fill["realized_pnl_usd"] - entry["fee_usd"]
            trades.append({
                "strategy_id": key.split("::t", 1)[0],
                "symbol": entry["symbol"],
                "entry_ts_ms": _iso_to_ms(entry["ts"]),
                "entry_ts": entry["ts"],
                "exit_reason": fill["reason"],
                "risk_usd": risk_usd,
                "net_usd": net,
                "r_net": net / risk_usd if risk_usd > 0 else 0.0,
                "run": Path(run_dir).name,
            })
    return sorted(trades, key=lambda t: t["entry_ts_ms"])


def build_windows(start: str, end: str, *, train_months: int, eval_months: int) -> list[dict]:
    """Rolling calendar windows; the eval span of window k starts where its
    train span ends. Windows whose eval span would start at/after `end` are
    dropped; the last eval span is clipped to `end`."""
    windows = []
    train_start = start
    while True:
        train_end = _add_months(train_start, train_months)
        eval_end = _add_months(train_end, eval_months)
        if _ms(train_end) >= _ms(end):
            break
        windows.append({
            "train_start_ms": _ms(train_start), "train_end_ms": _ms(train_end),
            "eval_start_ms": _ms(train_end), "eval_end_ms": min(_ms(eval_end), _ms(end)),
            "train_span": f"{train_start}->{train_end}",
            "eval_span": f"{train_end}->{min(eval_end, end)}",
        })
        train_start = _add_months(train_start, eval_months)
    return windows


def learn_thresholds(train_trades: list[dict], *, min_keep_fraction: float = 0.3,
                     min_keep_trades: int = 8) -> dict:
    """Deterministic grid search on the train window. Returns the frozen
    thresholds plus the train-side stats that justified them."""
    usable = [t for t in train_trades
              if t["features"]["channel_mid_slope_atr"] == t["features"]["channel_mid_slope_atr"]
              and t["features"]["efficiency_ratio_10"] == t["features"]["efficiency_ratio_10"]]
    if not usable:
        return {"slope_min": None, "er_min": None, "train_n": 0, "degenerate": True}

    def quantile(values: list[float], q: float) -> float:
        ordered = sorted(values)
        return ordered[min(int(q * len(ordered)), len(ordered) - 1)]

    slopes = [t["features"]["channel_mid_slope_atr"] for t in usable]
    ers = [t["features"]["efficiency_ratio_10"] for t in usable]
    floor = max(min_keep_trades, int(min_keep_fraction * len(usable)))
    best = None
    for sq in QUANTILE_GRID:
        slope_min = None if sq is None else quantile(slopes, sq)
        for eq in QUANTILE_GRID:
            er_min = None if eq is None else quantile(ers, eq)
            passing = [t for t in usable
                       if (slope_min is None or t["features"]["channel_mid_slope_atr"] >= slope_min)
                       and (er_min is None or t["features"]["efficiency_ratio_10"] >= er_min)]
            if len(passing) < floor:
                continue
            mean_r = sum(t["r_net"] for t in passing) / len(passing)
            # maximize mean R; ties -> keep more trades -> lower quantiles.
            score = (round(mean_r, 10), len(passing),
                     -(sq if sq is not None else -1), -(eq if eq is not None else -1))
            if best is None or score > best[0]:
                best = (score, {
                    "slope_min": slope_min, "er_min": er_min,
                    "slope_quantile": sq, "er_quantile": eq,
                    "train_n": len(usable), "train_pass_n": len(passing),
                    "train_mean_r_all": round(sum(t["r_net"] for t in usable) / len(usable), 4),
                    "train_mean_r_pass": round(mean_r, 4),
                })
    return best[1] if best else {"slope_min": None, "er_min": None,
                                 "train_n": len(usable), "degenerate": True}


def apply_thresholds(trade: dict, thresholds: dict) -> tuple[str, str]:
    """(verdict, reason) for one trade under frozen thresholds."""
    slope = trade["features"]["channel_mid_slope_atr"]
    er = trade["features"]["efficiency_ratio_10"]
    if slope != slope or er != er:
        return "allowed", "features unavailable (warmup) -> fail-open"
    slope_min, er_min = thresholds.get("slope_min"), thresholds.get("er_min")
    if slope_min is not None and slope < slope_min:
        return "blocked", f"channel_mid_slope_atr {slope:.4f} < {slope_min:.4f}"
    if er_min is not None and er < er_min:
        return "blocked", f"efficiency_ratio_10 {er:.4f} < {er_min:.4f}"
    parts = []
    if slope_min is not None:
        parts.append(f"channel_mid_slope_atr {slope:.4f} >= {slope_min:.4f}")
    if er_min is not None:
        parts.append(f"efficiency_ratio_10 {er:.4f} >= {er_min:.4f}")
    return "allowed", " and ".join(parts) if parts else "no filter learned (train preferred keeping all)"


def _balance_walk(trades: list[dict]) -> dict:
    """Counterfactual fixed-size balance walk (no compounding resize): the
    chronological sum of net USD, its max drawdown vs running peak."""
    balance = peak = 0.0
    max_dd = 0.0
    for trade in sorted(trades, key=lambda t: t["entry_ts_ms"]):
        balance += trade["net_usd"]
        peak = max(peak, balance)
        max_dd = max(max_dd, peak - balance)
    return {"net_usd": round(balance, 2), "max_dd_usd_vs_peak": round(max_dd, 2)}


def _aggregate(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    rs = [t["r_net"] for t in trades]
    return {
        "n": len(trades),
        "mean_r_net": round(sum(rs) / len(rs), 4),
        "win_rate": round(sum(1 for r in rs if r > 0) / len(rs), 4),
        "net_usd": round(sum(t["net_usd"] for t in trades), 2),
    }


def run_regime_study(run_dirs: list[Path], provider: SnapshotProvider, *,
                     start: str, end: str, train_months: int = 12, eval_months: int = 6,
                     feature_timeframe: str = "4h",
                     min_keep_fraction: float = 0.3, min_keep_trades: int = 8) -> dict:
    """Full walk-forward shadow study over the trades of the given runs.
    Returns {verdicts, report}; the CLI persists both."""
    windows = build_windows(start, end, train_months=train_months, eval_months=eval_months)
    all_trades: list[dict] = []
    for run_dir in run_dirs:
        all_trades.extend(load_trades(Path(run_dir)))
    for trade in all_trades:
        candles = provider.as_of(trade["symbol"], feature_timeframe,
                                 FEATURE_WINDOW_BARS, trade["entry_ts_ms"])
        trade["features"] = regime_features(candles)

    verdicts: list[dict] = []
    thresholds_by_window: dict[str, dict] = {}
    strategies = sorted({t["strategy_id"] for t in all_trades})
    for sid in strategies:
        strategy_trades = [t for t in all_trades if t["strategy_id"] == sid]
        evaluated_ids = set()
        for w_index, window in enumerate(windows):
            train = [t for t in strategy_trades
                     if window["train_start_ms"] <= t["entry_ts_ms"] < window["train_end_ms"]]
            evaluation = [t for t in strategy_trades
                          if window["eval_start_ms"] <= t["entry_ts_ms"] < window["eval_end_ms"]]
            thresholds = learn_thresholds(train, min_keep_fraction=min_keep_fraction,
                                          min_keep_trades=min_keep_trades)
            thresholds_by_window[f"{sid}/w{w_index}"] = {**thresholds, **{
                k: window[k] for k in ("train_span", "eval_span")}}
            for trade in evaluation:
                verdict, reason = apply_thresholds(trade, thresholds)
                evaluated_ids.add(id(trade))
                verdicts.append({
                    "strategy_id": sid, "run": trade["run"], "entry_ts": trade["entry_ts"],
                    "window": f"w{w_index}", "verdict": verdict, "reason": reason,
                    "features": {k: (round(v, 6) if v == v else None)
                                 for k, v in trade["features"].items()},
                    "thresholds": {k: thresholds.get(k) for k in ("slope_min", "er_min")},
                    "r_net": round(trade["r_net"], 6), "net_usd": round(trade["net_usd"], 2),
                    "exit_reason": trade["exit_reason"],
                    "entry_ts_ms": trade["entry_ts_ms"], "risk_usd": trade["risk_usd"],
                })
        for trade in strategy_trades:
            if id(trade) not in evaluated_ids:
                verdicts.append({
                    "strategy_id": sid, "run": trade["run"], "entry_ts": trade["entry_ts"],
                    "window": None, "verdict": "train_only", "reason": "not covered by any eval window",
                    "r_net": round(trade["r_net"], 6), "net_usd": round(trade["net_usd"], 2),
                    "entry_ts_ms": trade["entry_ts_ms"], "risk_usd": trade["risk_usd"],
                })

    verdicts.sort(key=lambda v: (v["strategy_id"], v["entry_ts_ms"]))
    report = {"windows": [w["train_span"] + " | eval " + w["eval_span"] for w in windows],
              "thresholds_by_window": thresholds_by_window, "per_strategy": {}}
    for sid in strategies:
        oos = [v for v in verdicts if v["strategy_id"] == sid and v["verdict"] in ("allowed", "blocked")]
        allowed = [v for v in oos if v["verdict"] == "allowed"]
        blocked = [v for v in oos if v["verdict"] == "blocked"]
        walk_all = _balance_walk(oos)
        walk_allowed = _balance_walk(allowed)
        report["per_strategy"][sid] = {
            "oos_all": _aggregate(oos), "oos_allowed": _aggregate(allowed),
            "oos_blocked": _aggregate(blocked),
            "trades_kept_fraction": round(len(allowed) / len(oos), 4) if oos else None,
            "forgone_profits_usd": round(sum(v["net_usd"] for v in blocked if v["net_usd"] > 0), 2),
            "avoided_losses_usd": round(-sum(v["net_usd"] for v in blocked if v["net_usd"] < 0), 2),
            "balance_walk_all": walk_all, "balance_walk_filtered": walk_allowed,
            "train_only_trades": sum(1 for v in verdicts
                                     if v["strategy_id"] == sid and v["verdict"] == "train_only"),
        }
    return {"verdicts": verdicts, "report": report}
