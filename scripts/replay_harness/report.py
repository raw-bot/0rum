"""Aggregation and comparison report for replay runs.

One block per config: P&L, PF, drawdown, exposure, fees, rejection reasons,
R distributions (legacy gross ATR-R, net ATR-R, net stop-R), profit
concentration (top-5 share) and worst losing streak — the statistics the
methodology review asked for before any conclusion is allowed.
"""

from __future__ import annotations

import json
import math
from pathlib import Path


def _percentiles(values: list[float], points=(10, 25, 50, 75, 90)) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    out = {}
    for p in points:
        k = (len(ordered) - 1) * p / 100
        lo, hi = int(math.floor(k)), int(math.ceil(k))
        out[f"p{p}"] = round(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo), 4)
    return out


def summarize_run(result: dict) -> dict:
    trades = result["trades"]
    curve = result["equity_curve"]
    config = result["config"]

    wins = [t for t in trades if t["account_net_pnl"] > 0]
    gross_profit = sum(t["account_net_pnl"] for t in trades if t["account_net_pnl"] > 0)
    gross_loss = -sum(t["account_net_pnl"] for t in trades if t["account_net_pnl"] <= 0)

    peak, max_dd = -math.inf, 0.0
    for _, equity in curve:
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    streak = worst_streak = 0
    for t in trades:
        streak = streak + 1 if t["account_net_pnl"] <= 0 else 0
        worst_streak = max(worst_streak, streak)

    top5 = sorted((t["account_net_pnl"] for t in wins), reverse=True)[:5]
    exposure = (sum(t["holding_ms"] for t in trades)
                / (curve[-1][0] - curve[0][0])) if curve and trades else 0.0

    rejections: dict[str, int] = {}
    for d in result["decisions"]:
        if d["action"] == "rejected":
            rejections[d["reason"]] = rejections.get(d["reason"], 0) + 1

    start = config["starting_balance"]
    final = curve[-1][1] if curve else start
    net = sum(t["account_net_pnl"] for t in trades)

    return {
        "config": config["name"],
        "sizing": config["sizing"],
        "fill": config["fill"],
        "target_ref": config["target_ref"],
        "risk_pct": config["risk_pct"],
        "n_candidates": len(result["decisions"]),  # one decision per candidate
        "n_trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 4) if trades else None,
        "net_pnl_usd": round(net, 2),
        "net_return_pct": round(100 * net / start, 2),
        "final_equity_usd": round(final, 2),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
        "max_drawdown_usd": round(max_dd, 2),
        "max_drawdown_pct_of_start": round(100 * max_dd / start, 2),
        "exposure_frac": round(exposure, 4),
        "total_fees_usd": round(sum(t["total_fees"] for t in trades), 2),
        "top5_share_of_gross_profit": round(sum(top5) / gross_profit, 3) if gross_profit > 0 else None,
        "max_consecutive_losses": worst_streak,
        "sl_tp_collisions": sum(1 for t in trades if t.get("sl_tp_collision")),
        "gap_through_exits": sum(1 for t in trades if t.get("gap_through")),
        "rejections": rejections,
        "r_legacy_gross": _percentiles([t["r_legacy_gross"] for t in trades]),
        "r_atr_net": _percentiles([t["r_atr_net"] for t in trades]),
        "r_stop_net": _percentiles([t["r_stop_net"] for t in trades
                                    if math.isfinite(t["r_stop_net"])]),
        "mean_r_stop_net": (round(sum(t["r_stop_net"] for t in trades
                                      if math.isfinite(t["r_stop_net"]))
                                  / max(1, sum(1 for t in trades
                                               if math.isfinite(t["r_stop_net"]))), 4)
                            if trades else None),
    }


def stop_atr_attribution(trades: list[dict], edges=(0.0, 0.8, 1.4, 2.0, 3.0, math.inf)) -> list[dict]:
    """E[R_stop_net | stop_distance/ATR bucket] — the diagnostic the review
    called the most important one."""
    out = []
    for lo, hi in zip(edges, edges[1:]):
        bucket = [t for t in trades
                  if lo <= t["features"]["stop_distance_atr"] < hi
                  and math.isfinite(t["r_stop_net"])]
        if not bucket:
            out.append({"stop_atr_range": f"[{lo}, {hi})", "n": 0})
            continue
        rs = [t["r_stop_net"] for t in bucket]
        out.append({
            "stop_atr_range": f"[{lo}, {hi})",
            "n": len(bucket),
            "mean_r_stop_net": round(sum(rs) / len(rs), 4),
            "win_rate": round(sum(1 for r in rs if r > 0) / len(rs), 3),
            "net_pnl_usd": round(sum(t["account_net_pnl"] for t in bucket), 2),
        })
    return out


def write_report(run_dir: Path, results: list[dict], *, data_fingerprint: str,
                 config_hash: str, extra: dict | None = None) -> Path:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "data_fingerprint_sha256": data_fingerprint,
        "config_sha256": config_hash,
        "summaries": [summarize_run(r) for r in results],
        "stop_atr_attribution": {
            r["config"]["name"]: stop_atr_attribution(r["trades"]) for r in results
        },
        **(extra or {}),
    }
    path = run_dir / "report.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    for result in results:
        (run_dir / f"trades_{result['config']['name']}.json").write_text(
            json.dumps(result["trades"], indent=1, default=str))
    return path
