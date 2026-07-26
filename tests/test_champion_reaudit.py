from pathlib import Path
from unittest.mock import patch

import yaml

from scripts import champion_reaudit


def _write_state(tmp_path: Path) -> None:
    (tmp_path / "strategy.yaml").write_text(yaml.safe_dump({"version": "01", "entry": {}, "exit": {}}))
    (tmp_path / "goal.yaml").write_text(yaml.safe_dump({"asset": "BTC/USDT"}))


def _trade(pnl: float) -> dict:
    return {"net_pnl_usd": pnl}


def _run(tmp_path: Path, trades: list[dict], **kwargs) -> dict:
    with (
        patch.object(champion_reaudit, "STRATEGY_PATH", tmp_path / "strategy.yaml"),
        patch.object(champion_reaudit, "GOAL_PATH", tmp_path / "goal.yaml"),
        patch("orum.dsl.backtest.load_history", return_value=[{"ts": i} for i in range(10)]),
        patch("orum.dsl.backtest.simulate", return_value={"trades": trades}),
    ):
        return champion_reaudit.run_reaudit(**kwargs)


def test_run_reaudit_flags_insufficient_evidence_below_minimum_trades(tmp_path):
    _write_state(tmp_path)

    report = _run(tmp_path, [_trade(10), _trade(-5)])

    assert report["verdict"] == "insufficient_data"
    assert report["checks"]["g9_minimum_evidence"]["trade_count"] == 2
    assert "g1_oos_objective" not in report["checks"]


def test_run_reaudit_conforming_when_profitable_throughout(tmp_path):
    _write_state(tmp_path)
    trades = [_trade(10)] * 9  # 9 wins, no losses

    report = _run(tmp_path, trades, folds=3)

    assert report["verdict"] == "conforming"
    assert report["checks"]["g1_oos_objective"]["status"] == "pass"
    assert report["checks"]["g2_walk_forward"]["status"] == "pass"


def test_run_reaudit_detects_drift_confined_to_the_latest_fold(tmp_path):
    _write_state(tmp_path)
    # Regression scenario for ADR-011: overall window is still net positive
    # (G1 passes) but the most recent fold is a run of losses -- exactly the
    # "decay in the tail, invisible on the aggregate" pattern this audit
    # exists to catch.
    trades = [_trade(10)] * 6 + [_trade(-10)] * 3

    report = _run(tmp_path, trades, folds=3)

    assert report["verdict"] == "drift_detected"
    assert report["checks"]["g1_oos_objective"]["status"] == "pass"
    assert report["checks"]["g2_walk_forward"]["status"] == "fail"
