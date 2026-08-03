import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import yaml

from orum import dashboard


class DashboardStateTests(unittest.TestCase):
    def test_snapshot_summarizes_bot_state_and_guardrails(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "goal.yaml").write_text(
                yaml.safe_dump(
                    {
                        "asset": "BTC/USDT",
                        "target_return_30d": 0.07,
                        "soft_drawdown": 0.03,
                        "max_drawdown": 0.05,
                        "emergency_stop_drawdown": 0.06,
                        "min_sharpe": 1.3,
                        "reflection_every": 10,
                        "starting_balance_usd": 10000,
                    }
                )
            )
            (state / "strategy.yaml").write_text(
                yaml.safe_dump(
                    {
                        "version": "01",
                        "entry": {"threshold": 30},
                        "position_size_r": 0.5,
                        "stop_loss_pct": 2.0,
                        "take_profit_pct": 3.0,
                    }
                )
            )
            (state / "heartbeat.json").write_text(
                json.dumps(
                    {
                        "ts": "2026-06-01T07:25:09Z",
                        "rsi": 31.5,
                        "last_price": 102.0,
                        "price_source": "binance_public",
                        "decision_action": "manage_position",
                        "decision_reason": "Position remains open.",
                    }
                )
            )
            (state / "open_position.json").write_text(
                json.dumps(
                    {
                        "asset": "BTC/USDT",
                        "direction": "long",
                        "entry_price": 100.0,
                        "notional_usd": 2500.0,
                        "risk_usd": 50.0,
                        "qty_base": 25.0,
                        "opened_at": "2026-06-01T07:20:09+00:00",
                    }
                )
            )
            # Source of truth for stats moved from the retired mono-asset worker's
            # trades.jsonl to the unified paper ledger (paper_fills.jsonl, "close"
            # actions). trades.jsonl is seeded with junk on purpose to prove the
            # dashboard now IGNORES it.
            (state / "trades.jsonl").write_text(
                "\n".join(json.dumps({"pnl_pct": 9.9, "ts": f"junk{i}"}) for i in range(5)) + "\n"
            )
            paper_fills = [
                {"action": "open", "ts": "t0", "strategy_id": "eth_donchian", "symbol": "ETH/USDT",
                 "side": "long", "price": 2000.0, "qty": 1.0},  # opens are not closed trades -> ignored
                {"action": "close", "ts": "t1", "strategy_id": "eth_donchian", "symbol": "ETH/USDT",
                 "side": "long", "entry_px": 2000.0, "price": 2100.0, "qty": 1.0, "realized_pnl_usd": 100.0, "r": 1.0},
                {"action": "close", "ts": "t2", "strategy_id": "btc_ak_macd_4h", "symbol": "BTC/USDT",
                 "side": "long", "entry_px": 60000.0, "price": 58000.0, "qty": 0.1, "realized_pnl_usd": -200.0, "r": -1.0},
                {"action": "close", "ts": "t3", "strategy_id": "eth_donchian", "symbol": "ETH/USDT",
                 "side": "long", "entry_px": 2050.0, "price": 2075.0, "qty": 2.0, "realized_pnl_usd": 50.0, "r": 0.5},
            ]
            (state / "paper_fills.jsonl").write_text("\n".join(json.dumps(f) for f in paper_fills) + "\n")
            (state / "hypotheses.jsonl").write_text(
                json.dumps({"changed": False, "score": 0.12, "reason": "hold", "ts": "h1"}) + "\n"
            )
            (state / "orum_watcher.json").write_text(
                json.dumps(
                    {
                        "ts": datetime.now(UTC).isoformat(),
                        "status": "standby",
                        "mode": "local_orum_watcher",
                        "reflection_every": 10,
                        "trades_seen": 3,
                        "trades_since_reflection": 3,
                        "detail": "Waiting for 7 more closed trades before 0rum reflection.",
                    }
                )
            )
            (state / "history").mkdir()

            with patch.object(dashboard, "STATE_DIR", state):
                snapshot = dashboard.build_snapshot()

        self.assertEqual(snapshot["asset"], "BTC/USDT")
        self.assertEqual(snapshot["trade_count"], 3)
        self.assertEqual(snapshot["reflection"]["remaining"], 7)
        self.assertEqual(snapshot["strategy"]["version"], "01")
        self.assertEqual(snapshot["last_price"], 102.0)
        self.assertEqual(snapshot["heartbeat"]["price_source"], "binance_public")
        self.assertTrue(any("action=manage_position" in item["detail"] for item in snapshot["activity"]))
        self.assertTrue(snapshot["open_position"]["active"])
        self.assertAlmostEqual(snapshot["open_position"]["unrealized_pnl_pct"], 0.02)
        self.assertAlmostEqual(snapshot["open_position"]["unrealized_pnl_usd"], 50.0)
        self.assertAlmostEqual(snapshot["open_position"]["stop_price"], 98.0)
        self.assertAlmostEqual(snapshot["open_position"]["take_profit_price"], 103.0)
        self.assertEqual(snapshot["latest_hypothesis"]["reason"], "hold")
        self.assertEqual(snapshot["decisions"][0]["decision"], "hold")
        self.assertEqual(snapshot["decisions"][0]["score"], 0.12)
        self.assertEqual(snapshot["engine"]["label"], "0rum watcher connected")
        self.assertGreaterEqual(len(snapshot["activity"]), 1)
        self.assertEqual(len(snapshot["candles"]), 3)
        self.assertIn("close", snapshot["candles"][0])
        self.assertEqual(len(snapshot["equity_curve"]), 3)
        self.assertIn("equity", snapshot["equity_curve"][0])
        self.assertAlmostEqual(snapshot["portfolio"]["starting_balance_usd"], 10000)
        # From the 3 paper close fills (+100, -200, +50), not the junk trades.jsonl.
        self.assertAlmostEqual(snapshot["portfolio"]["balance_usd"], 9950.0, places=2)
        self.assertAlmostEqual(snapshot["portfolio"]["pnl_usd"], -50.0, places=2)
        self.assertIn(snapshot["guardrail"]["status"], {"normal", "caution", "review", "kill"})
        # No champion_reaudit_status.json written in this fixture (ADR-011).
        self.assertEqual(snapshot["champion_reaudit"]["status"], "not_yet_audited")

    def test_champion_reaudit_status_surfaces_drift_from_the_reaudit_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "champion_reaudit_status.json").write_text(
                json.dumps({"verdict": "drift_detected", "audited_at": "2026-08-26T00:00:00+00:00"})
            )

            status = dashboard._champion_reaudit_status(state)

        self.assertEqual(status["status"], "drift_detected")
        self.assertEqual(status["audited_at"], "2026-08-26T00:00:00+00:00")

    def test_snapshot_flags_stale_paper_engine(self):
        # The worker card now reflects the paper engine's freshness
        # (paper_equity.jsonl), not the retired mono-asset heartbeat. A cycle
        # from 2026-06-01 is far past the 45-minute budget -> presumed down.
        import tempfile
        from unittest.mock import patch as _patch

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "goal.yaml").write_text("asset: BTC/USDT\n")
            (state / "strategy.yaml").write_text("version: '01'\n")
            (state / "paper_equity.jsonl").write_text(
                json.dumps({"ts": "2026-06-01T07:25:09+00:00", "equity_usd": 10000.0}) + "\n"
            )
            with _patch.object(dashboard, "STATE_DIR", state):
                snapshot = dashboard.build_snapshot()

        self.assertTrue(snapshot["worker"]["stale"])
        self.assertFalse(snapshot["worker"]["running"])
        self.assertGreater(snapshot["worker"]["heartbeat_age_seconds"], 2700)

    def test_unified_position_exit_metadata_and_legacy_history_stay_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "goal.yaml").write_text("asset: BTC/USDT\nstarting_balance_usd: 10000\n")
            (state / "strategy.yaml").write_text("version: '01'\n")
            (state / "paper_positions.json").write_text(json.dumps({
                "balance_usd": 9995.0,
                "positions": {"btc_ak_macd_4h": {
                    "strategy_id": "btc_ak_macd_4h", "symbol": "BTC/USDT", "side": "long",
                    "qty": .1, "entry_px": 60_000, "notional_usd": 6_000, "risk_pct": .02,
                    "opened_ts": "2026-07-13T06:00:00+00:00", "entry_reason": "AK",
                    "exit_policy": "structural_bracket", "monitor_timeframe": "15m",
                    "stop_loss_price": 59_000, "take_profit_price": 61_500,
                    "sl_basis": "baseline", "reward_risk_ratio": 1.5,
                }},
            }))
            (state / "trades.jsonl").write_text(json.dumps({
                "ts": datetime.now(UTC).isoformat(), "asset": "BTC/USDT",
                "direction": "long", "entry_price": 60_000, "exit_price": 60_100,
                "net_pnl_usd": 10.0, "exit_reason": "dsl_exit",
            }) + "\n")
            with patch.object(dashboard, "STATE_DIR", state), \
                 patch.object(dashboard, "worker_running", return_value=True):
                snapshot = dashboard.build_snapshot()

        position = snapshot["paper"]["open_positions"][0]
        self.assertEqual(position["stop_loss_price"], 59_000)
        self.assertEqual(position["take_profit_price"], 61_500)
        self.assertEqual(position["exit_policy"], "structural_bracket")
        self.assertEqual(snapshot["trade_count"], 0)
        self.assertEqual(snapshot["legacy_audit"]["trade_count_7d"], 1)
        self.assertEqual(snapshot["legacy_audit"]["net_pnl_usd_7d"], 10.0)
        self.assertFalse(snapshot["legacy_audit"]["authoritative"])
        self.assertTrue(snapshot["legacy_audit"]["process_running"])

    def test_paper_state_groups_tranches_by_stable_strategy_and_actual_risk(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "paper_positions.json").write_text(json.dumps({
                "balance_usd": 10_000.0,
                "positions": {
                    "btc_utbot_m15_h1": {
                        "strategy_id": "btc_utbot_m15_h1",
                        "position_id": "btc_utbot_m15_h1",
                        "symbol": "BTC/USDT", "side": "long", "qty": 1.0,
                        "entry_px": 100.0, "notional_usd": 100.0,
                        "risk_pct": 0.02, "atr_risk": 4.0,
                        "risk_distance": 10.0, "stop_loss_price": 90.0,
                    },
                    "btc_utbot_m15_h1::t2": {
                        "strategy_id": "btc_utbot_m15_h1",
                        "position_id": "btc_utbot_m15_h1::t2",
                        "symbol": "BTC/USDT", "side": "long", "qty": 2.0,
                        "entry_px": 110.0, "notional_usd": 220.0,
                        "risk_pct": 0.01, "atr_risk": 5.0,
                        "risk_distance": 20.0, "stop_loss_price": 90.0,
                    },
                },
            }))
            with patch.object(dashboard, "STATE_DIR", state):
                paper = dashboard._paper_state({"starting_balance_usd": 10_000.0})

        self.assertEqual(paper["open_count"], 2)
        self.assertEqual(len(paper["open_positions"]), 1)
        position = paper["open_positions"][0]
        self.assertEqual(position["strategy_id"], "btc_utbot_m15_h1")
        self.assertEqual(position["tranche_count"], 2)
        self.assertEqual(position["notional_usd"], 320.0)
        self.assertEqual(position["stop_risk_usd"], 50.0)
        self.assertAlmostEqual(position["entry_px"], 320.0 / 3.0)
        self.assertEqual(
            {tranche["position_id"] for tranche in position["tranches"]},
            {"btc_utbot_m15_h1", "btc_utbot_m15_h1::t2"},
        )
