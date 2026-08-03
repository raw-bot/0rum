import unittest

from orum.strategies import StrategyEngineError, load_engine, register_engine
from orum.strategies.ak_macd import AkMacdEngine
from orum.strategies.base import StrategyContext
from orum.strategies.native_dsl import NativeDslEngine
from orum.strategies.opening_range import OpeningRangeEngine
from orum.strategies.utbot_mtf import UtBotMtfEngine


class LoadBuiltinEnginesTests(unittest.TestCase):
    def test_loads_native_dsl_by_built_in_name(self):
        goal = {"strategy_engine": {"name": "native_dsl", "params": {"version": "01", "entry": {}, "exit": {}}}}
        engine = load_engine(goal)
        self.assertIsInstance(engine, NativeDslEngine)

    def test_loads_ak_macd_by_built_in_name(self):
        goal = {"strategy_engine": {"name": "ak_macd", "params": {"confirmation_bars": 3}}}
        engine = load_engine(goal)
        self.assertIsInstance(engine, AkMacdEngine)
        self.assertEqual(engine._params.confirmation_bars, 3)

    def test_params_defaults_to_empty_dict_when_absent(self):
        goal = {"strategy_engine": {"name": "ak_macd"}}
        engine = load_engine(goal)  # must not raise on a missing params block
        self.assertIsInstance(engine, AkMacdEngine)

    def test_loads_utbot_mtf_by_built_in_name(self):
        engine = load_engine({"strategy_engine": {"name": "utbot_mtf"}})
        self.assertIsInstance(engine, UtBotMtfEngine)
        self.assertEqual(engine.required_timeframes, ["15m", "1h"])

    def test_loads_opening_range_by_built_in_name(self):
        engine = load_engine({"strategy_engine": {"name": "opening_range"}})
        self.assertIsInstance(engine, OpeningRangeEngine)
        self.assertEqual(engine.required_timeframes, ["1d"])

    def test_kama_squeeze_is_not_registry_activatable(self):
        with self.assertRaises(StrategyEngineError):
            load_engine({"strategy_engine": {"name": "kama_squeeze"}})


class LoadByModulePathTests(unittest.TestCase):
    def test_unrecognized_name_with_module_path_imports_engine_class(self):
        goal = {"strategy_engine": {"name": "native_dsl_via_module", "module": "orum.strategies.native_dsl"}}
        engine = load_engine(goal)
        self.assertIsInstance(engine, NativeDslEngine)

    def test_built_in_name_wins_over_module_path(self):
        # name="ak_macd" is built in, so an (intentionally wrong) module path
        # is never even consulted -- name resolution is table-first.
        goal = {"strategy_engine": {"name": "ak_macd", "module": "this.module.does.not.exist"}}
        engine = load_engine(goal)
        self.assertIsInstance(engine, AkMacdEngine)


class RejectionTests(unittest.TestCase):
    def test_missing_strategy_engine_block_raises(self):
        with self.assertRaises(StrategyEngineError):
            load_engine({})

    def test_missing_name_raises(self):
        with self.assertRaises(StrategyEngineError):
            load_engine({"strategy_engine": {"module": "orum.strategies.native_dsl"}})

    def test_unknown_name_without_module_raises(self):
        with self.assertRaises(StrategyEngineError):
            load_engine({"strategy_engine": {"name": "donchian_breakout"}})

    def test_unimportable_module_raises_strategy_engine_error_not_import_error(self):
        with self.assertRaises(StrategyEngineError):
            load_engine({"strategy_engine": {"name": "ghost", "module": "orum.strategies.does_not_exist"}})

    def test_module_without_engine_class_raises(self):
        with self.assertRaises(StrategyEngineError):
            load_engine({"strategy_engine": {"name": "ghost", "module": "orum.strategies.base"}})


class RegisterEngineTests(unittest.TestCase):
    def test_register_engine_makes_a_short_name_resolvable(self):
        class _DummyEngine:
            name = "dummy"
            version = "0"
            required_timeframes: list[str] = []
            required_indicators: list[str] = []
            warmup_period = 0

            def init(self, config: dict) -> None:
                self.config = config

            def on_candle(self, candle: dict, context: StrategyContext):
                return None

        register_engine("dummy", _DummyEngine)
        try:
            engine = load_engine({"strategy_engine": {"name": "dummy", "params": {"x": 1}}})
            self.assertIsInstance(engine, _DummyEngine)
            self.assertEqual(engine.config, {"x": 1})
        finally:
            from orum.strategies import _ENGINES

            del _ENGINES["dummy"]  # don't leak test state into other tests


if __name__ == "__main__":
    unittest.main()
