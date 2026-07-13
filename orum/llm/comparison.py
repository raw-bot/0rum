"""Fair reference/evolving comparison over their shared outcome window."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


LANES = ("llm_reference", "llm_evolving")


def _metrics(rows: list[Mapping[str, object]]) -> dict[str, object]:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    liquidations = 0
    squared_errors: list[float] = []
    for row in sorted(rows, key=lambda item: int(item["exit_candle_ts"])):
        value = float(row.get("account_return", row["net_return_on_margin"]))
        equity *= max(0.0, 1 + value)
        peak = max(peak, equity)
        if peak:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
        liquidations += row.get("exit_reason") == "liquidation"
        if row.get("calibration_squared_error") is not None:
            squared_errors.append(float(row["calibration_squared_error"]))
    count = len(rows)
    compounded = equity - 1
    return {
        "decision_count": count,
        "compounded_return": compounded,
        "max_drawdown": max_drawdown,
        "calmar": None if max_drawdown == 0 else compounded / max_drawdown,
        "log_growth": None if equity == 0 else math.log(equity),
        "liquidation_rate": 0 if count == 0 else liquidations / count,
        "ruin_rate": 1 if equity == 0 else 0,
        "mean_calibration_error": (
            None if not squared_errors else math.fsum(squared_errors) / len(squared_errors)
        ),
    }


def compare_lanes(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    by_lane = {
        lane: [row for row in rows if row.get("lane") == lane]
        for lane in LANES
    }
    if all(by_lane.values()):
        start = max(min(int(row["exit_candle_ts"]) for row in by_lane[lane]) for lane in LANES)
        end = min(max(int(row["exit_candle_ts"]) for row in by_lane[lane]) for lane in LANES)
        common = start <= end
    else:
        start = end = None
        common = False
    selected = {
        lane: (
            [row for row in by_lane[lane] if start <= int(row["exit_candle_ts"]) <= end]
            if common else by_lane[lane]
        )
        for lane in LANES
    }
    if common and not all(selected.values()):
        common = False
        start = end = None
        selected = by_lane
    return {
        "coverage_status": "common_window" if common else "no_common_window",
        "common_cutoff_start": start if common else None,
        "common_cutoff_end": end if common else None,
        **{lane: _metrics(selected[lane]) for lane in LANES},
    }
