#!/usr/bin/env python3
"""Monthly self-re-audit of the live champion strategy (ADR-011).

Standalone and read-only: replays the current `state/strategy.yaml` over
recent market data through the same DSL backtest engine already used to gate
proposed mutations (orum/dsl/backtest.py -- see orum/reflect.py for the other
caller), and writes a pass/fail scorecard to
`state/champion_reaudit_status.json`.

This never changes strategy.yaml, never trades, and is not imported by
loop.py/worker/dashboard/producer -- run it manually or from your own
separate scheduled job. The dashboard only reads its output file.

G8 (per-year consistency, see workshop ADR-002) is intentionally not
implemented here: the 1m-candle history this script fetches is only
practical over weeks/months, not the multi-year span G8 needs. See
ADR-011 "Implementation reality check".
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

import yaml

from orum.dsl import backtest as dsl_backtest
from orum.dsl.migrate import risk_value, strategy_dsl_groups
from orum.fsio import atomic_write_json
from orum.paths import GOAL_PATH, STATE_DIR, STRATEGY_PATH

REAUDIT_WINDOW_DAYS = 60
REAUDIT_FOLDS = 3
MIN_TRADES_FOR_VERDICT = 5
# Recorded in Docs/decisions/ADR-002-lab-to-bot-promotion-contract.md
# (workshop repo): AK MACD 4h long-only, full-window baseline. Kept here as
# context only -- the pass/fail bar below is "still profitable at all"
# (>= 1.0), not "still beats this exact historical number".
CHAMPION_BASELINE_PROFIT_FACTOR = 1.53
OUTPUT_PATH = STATE_DIR / "champion_reaudit_status.json"


def _profit_factor(trades: list[dict]) -> float:
    gross_profit = sum(t["net_pnl_usd"] for t in trades if t["net_pnl_usd"] > 0)
    gross_loss = -sum(t["net_pnl_usd"] for t in trades if t["net_pnl_usd"] < 0)
    if gross_loss <= 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def _fold_profit_factors(trades: list[dict], folds: int) -> list[float]:
    fold_size = max(1, len(trades) // folds)
    return [
        _profit_factor(trades[i * fold_size : len(trades) if i == folds - 1 else (i + 1) * fold_size])
        for i in range(folds)
    ]


def run_reaudit(*, window_days: int = REAUDIT_WINDOW_DAYS, folds: int = REAUDIT_FOLDS) -> dict:
    strategy = yaml.safe_load(STRATEGY_PATH.read_text()) or {}
    goal = yaml.safe_load(GOAL_PATH.read_text()) or {}
    risk = {
        key: risk_value(strategy, key, default)
        for key, default in (
            ("stop_loss_pct", 2.0),
            ("take_profit_pct", 3.0),
            ("max_hold_candles", 30),
            ("position_size_r", 0.5),
            ("fee_rate", 0.0004),
        )
    }
    groups = strategy_dsl_groups(strategy)
    asset = goal.get("asset", "BTC/USDT")
    candles = dsl_backtest.load_history(asset, days=window_days)
    result = dsl_backtest.simulate(groups, risk, goal, candles)
    trades = result["trades"]

    report = {
        "audited_at": datetime.now(UTC).isoformat(),
        "window": {"asset": asset, "days": window_days, "candle_count": len(candles)},
        "baseline": {"profit_factor": CHAMPION_BASELINE_PROFIT_FACTOR},
        "checks": {},
        "verdict": "insufficient_data",
    }

    if len(trades) < MIN_TRADES_FOR_VERDICT:
        report["checks"]["g9_minimum_evidence"] = {
            "status": "fail",
            "trade_count": len(trades),
            "required": MIN_TRADES_FOR_VERDICT,
        }
        return report

    current_pf = _profit_factor(trades)
    g1_status = "pass" if current_pf >= 1.0 else "fail"
    report["checks"]["g1_oos_objective"] = {
        "status": g1_status,
        "current_profit_factor": current_pf,
        "baseline_profit_factor": CHAMPION_BASELINE_PROFIT_FACTOR,
        "trade_count": len(trades),
    }

    fold_pfs = _fold_profit_factors(trades, folds)
    g2_status = "pass" if fold_pfs[-1] >= 1.0 else "fail"
    report["checks"]["g2_walk_forward"] = {
        "status": g2_status,
        "fold_profit_factors": fold_pfs,
        "latest_fold_profit_factor": fold_pfs[-1],
    }

    report["verdict"] = "conforming" if g1_status == "pass" and g2_status == "pass" else "drift_detected"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-days", type=int, default=REAUDIT_WINDOW_DAYS)
    parser.add_argument("--folds", type=int, default=REAUDIT_FOLDS)
    args = parser.parse_args()

    report = run_reaudit(window_days=args.window_days, folds=args.folds)
    atomic_write_json(OUTPUT_PATH, report)
    print(f"champion re-audit: {report['verdict']} -> {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
