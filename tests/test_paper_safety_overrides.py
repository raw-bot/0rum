import json
import tempfile
import unittest
from pathlib import Path

from orum.portfolio.paper_engine import PaperEngine
from orum.strategies import register_engine
from orum.strategies.base import Side, Signal


def _candles(*, count: int = 20, start: int = 1_800_000_000_000) -> list[dict]:
    rows = []
    for index in range(count):
        close = 100.0
        rows.append({
            "ts": start + index * 900_000,
            "open": close,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": 1.0,
        })
    return rows


class ConfigurableSignalEngine:
    name = "paper_safety_signal"
    version = "1"
    required_timeframes: list[str] = []
    required_indicators: list[str] = []
    warmup_period = 1

    def init(self, config):
        self._side = Side(config.get("side", "long"))
        self._stop_offset = float(config.get("stop_offset", 10.0))

    def on_candle(self, candle, context):
        price = float(candle["close"])
        if self._side == Side.SHORT:
            return Signal(
                Side.SHORT,
                context.symbol,
                context.timeframe,
                "test short",
                suggested_stop=price + self._stop_offset,
                suggested_take_profit=price - 2.0 * self._stop_offset,
            )
        return Signal(
            Side.LONG,
            context.symbol,
            context.timeframe,
            "test long",
            suggested_stop=price - self._stop_offset,
        )


class PaperSafetyOverridesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        register_engine("paper_safety_signal", ConfigurableSignalEngine)

    @classmethod
    def tearDownClass(cls):
        from orum.strategies import _ENGINES

        _ENGINES.pop("paper_safety_signal", None)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.positions_path = root / "positions.json"
        self.fills_path = root / "fills.jsonl"
        self.equity_path = root / "equity.jsonl"
        self.shadow_path = root / "shadow.jsonl"
        self.market = {"15m": _candles(), "4h": _candles(count=220)}

    def tearDown(self):
        self._tmp.cleanup()

    def _provider(self, symbol, timeframe, limit):
        return [dict(row) for row in self.market[timeframe]][-limit:]

    def _strategy(self, strategy_id="btc"):
        return {
            "id": strategy_id,
            "engine": "paper_safety_signal",
            "symbol": "BTC/USDT",
            "timeframe": "15m",
            "risk_pct": 0.01,
            "entry_enabled": True,
            "params": {"side": "long", "stop_offset": 10.0},
        }

    def _engine(self, config):
        return PaperEngine(
            config,
            candle_provider=self._provider,
            positions_path=self.positions_path,
            fills_path=self.fills_path,
            equity_path=self.equity_path,
            shadow_regime_path=self.shadow_path,
        )

    def test_actual_stop_basis_sizes_and_budgets_on_real_stop_distance(self):
        config = {
            "starting_balance_usd": 10_000.0,
            "reentry_policy": "hold",
            "risk_sizing_basis": "actual_stop",
            "strategies": [self._strategy()],
        }

        summary = self._engine(config).run_cycle()

        fill = summary["fills"][0]
        self.assertAlmostEqual(fill["qty"], 10.0)
        self.assertAlmostEqual(fill["qty"] * fill["risk_distance"], 100.0)
        self.assertEqual(fill["risk_sizing_basis"], "actual_stop")

    def test_long_only_override_rejects_short_before_reversal_or_open(self):
        strategy = self._strategy()
        strategy["params"]["side"] = "short"
        config = {
            "starting_balance_usd": 10_000.0,
            "reentry_policy": "hold",
            "allowed_entry_sides": ["long"],
            "strategies": [strategy],
        }

        summary = self._engine(config).run_cycle()

        self.assertEqual(summary["intents"]["btc"], "short_disabled")
        self.assertEqual(summary["fills"], [])

    def test_next_open_uses_prior_closed_signal_and_following_candle_open(self):
        rows = _candles()
        rows[-2]["close"] = 100.0
        rows[-1].update(open=105.0, high=112.0, low=104.0, close=110.0)
        self.market["15m"] = rows
        config = {
            "starting_balance_usd": 10_000.0,
            "reentry_policy": "hold",
            "execution_mode": "next_open",
            "risk_sizing_basis": "actual_stop",
            "strategies": [self._strategy()],
        }

        summary = self._engine(config).run_cycle()

        self.assertEqual(summary["fills"][0]["price"], 105.0)
        self.assertEqual(summary["fills"][0]["stop_loss_price"], 90.0)

    def test_one_btc_thesis_blocks_second_strategy(self):
        config = {
            "starting_balance_usd": 10_000.0,
            "reentry_policy": "topup",
            "max_open_positions_by_symbol": {"BTC/USDT": 1},
            "merit_order": ["first", "second"],
            "strategies": [self._strategy("first"), self._strategy("second")],
        }

        summary = self._engine(config).run_cycle()

        self.assertEqual(summary["open_positions"], ["first"])
        self.assertEqual(summary["intents"]["second"], "position_cap_symbol")

    def test_drawdown_kill_switch_blocks_entries_but_does_not_mutate_positions(self):
        self.positions_path.write_text(json.dumps({
            "balance_usd": 8_000.0,
            "positions": {},
            "processed_candles": {},
        }))
        self.equity_path.write_text(json.dumps({
            "ts": "peak",
            "equity_usd": 10_000.0,
            "balance_usd": 10_000.0,
            "open_positions": 0,
        }) + "\n")
        config = {
            "starting_balance_usd": 10_000.0,
            "reentry_policy": "hold",
            "entry_drawdown_kill_pct": 0.05,
            "strategies": [self._strategy()],
        }

        summary = self._engine(config).run_cycle()

        self.assertEqual(summary["intents"]["btc"], "drawdown_kill_switch")
        self.assertEqual(summary["fills"], [])
        self.assertAlmostEqual(summary["entry_drawdown"], 0.20)

    def test_btc_notional_cap_shrinks_fill(self):
        strategy = self._strategy()
        strategy["params"]["stop_offset"] = 1.0
        config = {
            "starting_balance_usd": 10_000.0,
            "reentry_policy": "hold",
            "risk_sizing_basis": "actual_stop",
            "max_symbol_notional_pct": {"BTC/USDT": 0.5},
            "strategies": [strategy],
        }

        summary = self._engine(config).run_cycle()

        fill = summary["fills"][0]
        self.assertLessEqual(fill["notional_usd"], 5_000.0)
        self.assertTrue(fill["symbol_notional_capped"])

    def test_h4_filter_is_logged_in_shadow_without_blocking_entry(self):
        h4 = _candles(count=220)
        h4[-1]["close"] = 80.0
        self.market["4h"] = h4
        config = {
            "starting_balance_usd": 10_000.0,
            "reentry_policy": "hold",
            "shadow_regime_filters": {
                "BTC/USDT": {
                    "timeframe": "4h",
                    "ema_period": 200,
                    "enforce": False,
                }
            },
            "strategies": [self._strategy()],
        }

        summary = self._engine(config).run_cycle()

        self.assertEqual(summary["intents"]["btc"], "open")
        record = json.loads(self.shadow_path.read_text().splitlines()[0])
        self.assertFalse(record["passed"])
        self.assertFalse(record["enforced"])


if __name__ == "__main__":
    unittest.main()
