import unittest
from dataclasses import FrozenInstanceError

from orum.strategies.base import Side, Signal, StrategyContext, StrategyEngine


class SideTests(unittest.TestCase):
    def test_has_exactly_the_four_documented_values(self):
        self.assertEqual(
            {member.value for member in Side},
            {"long", "short", "flat", "exit"},
        )


class SignalTests(unittest.TestCase):
    def test_only_side_symbol_timeframe_entry_reason_are_required(self):
        signal = Signal(side=Side.LONG, symbol="BTC/USDT", timeframe="15m", entry_reason="flip_up confirmed")
        self.assertIsNone(signal.confidence)
        self.assertIsNone(signal.invalidation_reason)
        self.assertIsNone(signal.suggested_stop)
        self.assertIsNone(signal.suggested_take_profit)
        self.assertEqual(signal.strategy_metadata, {})

    def test_is_immutable(self):
        signal = Signal(side=Side.FLAT, symbol="BTC/USDT", timeframe="15m", entry_reason="no edge")
        with self.assertRaises(FrozenInstanceError):
            signal.side = Side.LONG

    def test_metadata_default_is_not_shared_between_instances(self):
        a = Signal(side=Side.FLAT, symbol="BTC/USDT", timeframe="15m", entry_reason="a")
        b = Signal(side=Side.FLAT, symbol="BTC/USDT", timeframe="15m", entry_reason="b")
        # dataclasses with a mutable default must use default_factory, or every
        # instance would share (and corrupt) the same dict.
        self.assertIsNot(a.strategy_metadata, b.strategy_metadata)

    def test_optional_fields_round_trip(self):
        signal = Signal(
            side=Side.SHORT,
            symbol="BTC/USDT",
            timeframe="15m",
            entry_reason="macd flip down confirmed",
            confidence=0.8,
            invalidation_reason="macd flip up",
            suggested_stop=64000.0,
            suggested_take_profit=60000.0,
            strategy_metadata={"engine": "ak_macd"},
        )
        self.assertEqual(signal.confidence, 0.8)
        self.assertEqual(signal.suggested_stop, 64000.0)
        self.assertEqual(signal.strategy_metadata["engine"], "ak_macd")


class StrategyContextTests(unittest.TestCase):
    def test_holds_only_read_only_market_inputs(self):
        context = StrategyContext(candles=[{"close": 1.0}], symbol="BTC/USDT", timeframe="15m")
        self.assertIsNone(context.regime)
        self.assertEqual(context.candles, [{"close": 1.0}])
        self.assertEqual(context.candles_by_timeframe, {})

    def test_exposes_no_executor_accounting_or_dashboard_access(self):
        # A strategy engine must not be able to reach execution/accounting/IO
        # through its context — those stay on the risk/execution side of the
        # seam. This pins the field list so a future field can't smuggle that
        # access back in unnoticed.
        field_names = {f.name for f in StrategyContext.__dataclass_fields__.values()}
        self.assertEqual(
            field_names,
            {"candles", "symbol", "timeframe", "regime", "candles_by_timeframe"},
        )


class StrategyEngineProtocolTests(unittest.TestCase):
    def test_dummy_implementation_satisfies_the_protocol(self):
        class _Dummy:
            name = "dummy"
            version = "0"
            required_timeframes = ["15m"]
            required_indicators: list[str] = []
            warmup_period = 0

            def init(self, config: dict) -> None:
                pass

            def on_candle(self, candle: dict, context: StrategyContext) -> Signal | None:
                return None

        self.assertIsInstance(_Dummy(), StrategyEngine)

    def test_object_missing_on_candle_does_not_satisfy_the_protocol(self):
        class _Incomplete:
            name = "incomplete"
            version = "0"
            required_timeframes: list[str] = []
            required_indicators: list[str] = []
            warmup_period = 0

            def init(self, config: dict) -> None:
                pass

        self.assertNotIsInstance(_Incomplete(), StrategyEngine)


if __name__ == "__main__":
    unittest.main()
