import unittest

from hermes_trading.reflect import _apply_hypothesis


class ApplyHypothesisTests(unittest.TestCase):
    def test_valid_value_inside_bounds_is_applied(self):
        strategy = {"entry": {"threshold": 30.0}}
        hypothesis = {"changed": True, "variable": "entry.threshold", "new_value": 25}

        result = _apply_hypothesis(strategy, hypothesis)

        self.assertTrue(result["changed"])
        self.assertEqual(strategy["entry"]["threshold"], 25.0)

    def test_out_of_bounds_value_is_clamped_and_annotated(self):
        strategy = {"stop_loss_pct": 2.0}
        hypothesis = {"changed": True, "variable": "stop_loss_pct", "new_value": 0.0, "reason": "model says so"}

        result = _apply_hypothesis(strategy, hypothesis)

        self.assertTrue(result["changed"])
        self.assertEqual(strategy["stop_loss_pct"], 0.5)
        self.assertEqual(result["new_value"], 0.5)
        self.assertEqual(result["requested_value"], 0.0)
        self.assertIn("clamped", result["reason"])

    def test_position_size_above_bound_is_clamped(self):
        strategy = {"position_size_r": 0.5}
        hypothesis = {"changed": True, "variable": "position_size_r", "new_value": 5.0}

        _apply_hypothesis(strategy, hypothesis)

        self.assertEqual(strategy["position_size_r"], 0.75)

    def test_missing_new_value_is_rejected_without_crashing(self):
        strategy = {"entry": {"threshold": 30.0}}
        hypothesis = {"changed": True, "variable": "entry.threshold", "new_value": None}

        result = _apply_hypothesis(strategy, hypothesis)

        self.assertFalse(result["changed"])
        self.assertTrue(result["rejected"])
        self.assertEqual(strategy["entry"]["threshold"], 30.0)

    def test_nan_value_is_rejected(self):
        strategy = {"stop_loss_pct": 2.0}
        hypothesis = {"changed": True, "variable": "stop_loss_pct", "new_value": float("nan")}

        result = _apply_hypothesis(strategy, hypothesis)

        self.assertFalse(result["changed"])
        self.assertEqual(strategy["stop_loss_pct"], 2.0)

    def test_unsupported_variable_is_rejected_without_crashing(self):
        strategy = {"entry": {"threshold": 30.0}}
        hypothesis = {"changed": True, "variable": "take_profit_pct", "new_value": 4.0}

        result = _apply_hypothesis(strategy, hypothesis)

        self.assertFalse(result["changed"])
        self.assertTrue(result["rejected"])
        self.assertEqual(strategy, {"entry": {"threshold": 30.0}})

    def test_unchanged_hypothesis_passes_through(self):
        strategy = {"entry": {"threshold": 30.0}}
        hypothesis = {"changed": False, "variable": None, "new_value": None}

        result = _apply_hypothesis(strategy, hypothesis)

        self.assertFalse(result["changed"])
        self.assertNotIn("rejected", result)


if __name__ == "__main__":
    unittest.main()
