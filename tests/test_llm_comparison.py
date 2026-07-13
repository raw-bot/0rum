import json

from orum.llm.comparison import compare_lanes


def _row(lane, decision, ts, value, *, reason="horizon", confidence=0.6, error=0.2):
    return {
        "lane": lane, "decision_id": decision, "exit_candle_ts": ts,
        "net_return_on_margin": value * 10, "account_return": value, "exit_reason": reason,
        "confidence": confidence, "calibration_squared_error": error,
    }


def test_comparison_uses_shared_intersection_and_reports_risk_metrics():
    rows = [
        _row("llm_reference", "r1", 1, 0.1),
        _row("llm_reference", "r2", 2, -0.2),
        _row("llm_evolving", "e1", 1, 0.2),
        _row("llm_evolving", "e2", 2, 0.1),
        _row("llm_evolving", "e3", 3, 2.0),
    ]

    result = compare_lanes(rows)

    assert result["common_cutoff_start"] == 1
    assert result["common_cutoff_end"] == 2
    assert result["llm_reference"]["decision_count"] == 2
    assert result["llm_evolving"]["decision_count"] == 2
    assert result["llm_reference"]["max_drawdown"] > 0
    assert result["llm_evolving"]["compounded_return"] > 0


def test_comparison_handles_ruin_and_missing_lane_without_fabrication():
    result = compare_lanes([
        _row("llm_reference", "r", 1, -1, reason="liquidation")
    ])

    assert result["coverage_status"] == "no_common_window"
    assert result["llm_reference"]["ruin_rate"] == 1
    assert result["llm_reference"]["log_growth"] is None
    assert result["llm_evolving"]["decision_count"] == 0
    json.dumps(result, allow_nan=False)


def test_comparison_refuses_interval_overlap_without_paired_exit_cohorts():
    result = compare_lanes([
        _row("llm_reference", "r1", 1, 0.1),
        _row("llm_reference", "r2", 3, 0.1),
        _row("llm_evolving", "e1", 2, 0.1),
    ])
    assert result["coverage_status"] == "no_common_window"
