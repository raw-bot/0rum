import unittest

from hermes_trading.dsl.evaluator import evaluate


def _candles_from_closes(closes):
    return [
        {"ts": 1700000000000 + i * 60000, "open": c, "high": c + 1, "low": c - 1, "close": float(c), "volume": 1.0}
        for i, c in enumerate(closes)
    ]


def _close_condition(**overrides):
    condition = {"indicator": "close", "operator": ">", "value": 100}
    condition.update(overrides)
    return condition


class CrossSemanticsTests(unittest.TestCase):
    """crosses_above <=> prev(lhs) <= prev(rhs) && curr(lhs) > curr(rhs)."""

    def test_crosses_above_fires_on_strict_cross(self):
        candles = _candles_from_closes([99.0, 101.0])
        result = evaluate({"logic": "AND", "conditions": [_close_condition(operator="crosses_above")]}, candles)
        self.assertTrue(result["triggered"])
        self.assertEqual(result["errors"], [])

    def test_crosses_above_fires_when_prev_equals_rhs(self):
        # The boundary case: prev == rhs counts as "was not above".
        candles = _candles_from_closes([100.0, 101.0])
        result = evaluate({"logic": "AND", "conditions": [_close_condition(operator="crosses_above")]}, candles)
        self.assertTrue(result["triggered"])

    def test_crosses_above_does_not_fire_without_a_cross(self):
        for closes in ([101.0, 102.0], [99.0, 100.0], [101.0, 99.0]):
            candles = _candles_from_closes(closes)
            result = evaluate(
                {"logic": "AND", "conditions": [_close_condition(operator="crosses_above")]}, candles
            )
            self.assertFalse(result["triggered"], closes)

    def test_crosses_below_fires_when_prev_equals_rhs(self):
        candles = _candles_from_closes([100.0, 99.0])
        result = evaluate({"logic": "AND", "conditions": [_close_condition(operator="crosses_below")]}, candles)
        self.assertTrue(result["triggered"])

    def test_prev_is_a_real_bar_not_a_copy_of_current(self):
        # Flat series: if prev were copied from current (Fincept pitfall #3),
        # rising would compare a value to itself and stay False here too, so
        # exercise both directions.
        rising = _candles_from_closes([100.0, 105.0])
        flat = _candles_from_closes([105.0, 105.0])
        cond = {"indicator": "close", "operator": "rising"}
        self.assertTrue(evaluate({"logic": "AND", "conditions": [cond]}, rising)["triggered"])
        self.assertFalse(evaluate({"logic": "AND", "conditions": [cond]}, flat)["triggered"])

    def test_indicator_vs_indicator_cross(self):
        # Fast SMA(2) crosses above slow SMA(4) after a V-shaped reversal.
        closes = [100.0, 90.0, 80.0, 90.0, 120.0]
        condition = {
            "indicator": "sma",
            "params": {"period": 2},
            "operator": "crosses_above",
            "compare_mode": "indicator",
            "compare_indicator": "sma",
            "compare_params": {"period": 4},
        }
        result = evaluate({"logic": "AND", "conditions": [condition]}, _candles_from_closes(closes))
        self.assertTrue(result["triggered"])
        self.assertEqual(result["errors"], [])


class LogicTests(unittest.TestCase):
    def test_and_requires_all_conditions(self):
        candles = _candles_from_closes([101.0, 102.0])
        true_cond = _close_condition(value=100)
        false_cond = _close_condition(value=200)
        self.assertTrue(evaluate({"logic": "AND", "conditions": [true_cond, true_cond]}, candles)["triggered"])
        self.assertFalse(evaluate({"logic": "AND", "conditions": [true_cond, false_cond]}, candles)["triggered"])

    def test_or_requires_any_condition(self):
        candles = _candles_from_closes([101.0, 102.0])
        true_cond = _close_condition(value=100)
        false_cond = _close_condition(value=200)
        self.assertTrue(evaluate({"logic": "OR", "conditions": [false_cond, true_cond]}, candles)["triggered"])
        self.assertFalse(evaluate({"logic": "OR", "conditions": [false_cond, false_cond]}, candles)["triggered"])

    def test_between_is_inclusive(self):
        candles = _candles_from_closes([100.0, 102.0])
        condition = _close_condition(operator="between", value=102, value2=110)
        self.assertTrue(evaluate({"logic": "AND", "conditions": [condition]}, candles)["triggered"])

    def test_regime_equality(self):
        rising = _candles_from_closes([100.0 + i for i in range(30)])
        cond_eq = {"indicator": "regime", "operator": "==", "value_str": "favorable"}
        cond_ne = {"indicator": "regime", "operator": "!=", "value_str": "unfavorable"}
        result = evaluate({"logic": "AND", "conditions": [cond_eq, cond_ne]}, rising)
        self.assertTrue(result["triggered"])
        self.assertEqual(result["errors"], [])


class ErrorSurfacingTests(unittest.TestCase):
    """Errors land in errors[], never swallowed, never replaced by a value."""

    def test_insufficient_warmup_is_an_explicit_error(self):
        candles = _candles_from_closes([100.0] * 5)
        condition = {"indicator": "rsi", "params": {"period": 14}, "operator": "<=", "value": 25}
        result = evaluate({"logic": "AND", "conditions": [condition]}, candles)
        self.assertFalse(result["triggered"])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("warm-up", result["errors"][0])
        self.assertIsNotNone(result["details"][0]["error"])

    def test_errored_condition_blocks_and_group(self):
        candles = _candles_from_closes([90.0, 95.0] * 10)
        healthy_true = _close_condition(value=1)
        broken = {"indicator": "rsi", "params": {"period": 50}, "operator": "<", "value": 100}
        result = evaluate({"logic": "AND", "conditions": [healthy_true, broken]}, candles)
        self.assertFalse(result["triggered"])
        self.assertTrue(result["errors"])
        self.assertTrue(result["details"][0]["met"])
        self.assertFalse(result["details"][1]["met"])

    def test_errored_condition_does_not_block_or_group(self):
        candles = _candles_from_closes([90.0, 95.0] * 10)
        healthy_true = _close_condition(value=1)
        broken = {"indicator": "rsi", "params": {"period": 50}, "operator": "<", "value": 100}
        result = evaluate({"logic": "OR", "conditions": [broken, healthy_true]}, candles)
        self.assertTrue(result["triggered"])
        self.assertTrue(result["errors"])

    def test_unknown_indicator_is_an_error_not_an_exception(self):
        candles = _candles_from_closes([100.0, 101.0])
        result = evaluate({"logic": "AND", "conditions": [{"indicator": "macd", "operator": ">", "value": 0}]}, candles)
        self.assertFalse(result["triggered"])
        self.assertIn("macd", result["errors"][0])

    def test_empty_group_is_an_error_and_never_triggers(self):
        result = evaluate({"logic": "AND", "conditions": []}, _candles_from_closes([100.0, 101.0]))
        self.assertFalse(result["triggered"])
        self.assertTrue(result["errors"])

    def test_result_shape_is_stable(self):
        candles = _candles_from_closes([100.0, 101.0])
        result = evaluate({"logic": "AND", "conditions": [_close_condition()]}, candles)
        self.assertEqual(set(result), {"triggered", "details", "errors"})
        detail = result["details"][0]
        self.assertEqual(set(detail), {"condition", "lhs", "rhs", "met", "error"})


if __name__ == "__main__":
    unittest.main()
