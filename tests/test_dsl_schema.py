import unittest

from hermes_trading.dsl.schema import DslValidationError, validate_group, validate_strategy_dsl


def _rsi_condition(**overrides):
    condition = {"indicator": "rsi", "params": {"period": 14}, "operator": "<=", "value": 25}
    condition.update(overrides)
    return condition


def _group(*conditions, logic="AND"):
    return {"logic": logic, "conditions": list(conditions)}


class SchemaAcceptanceTests(unittest.TestCase):
    def test_reference_strategy_validates(self):
        entry = _group(
            _rsi_condition(),
            {"indicator": "regime", "operator": "!=", "value_str": "unfavorable"},
        )
        exit_group = _group(_rsi_condition(operator=">=", value=60), logic="OR")
        validate_strategy_dsl(entry, exit_group)

    def test_indicator_cross_validates(self):
        validate_group(
            _group(
                {
                    "indicator": "sma",
                    "params": {"period": 10},
                    "operator": "crosses_above",
                    "compare_mode": "indicator",
                    "compare_indicator": "sma",
                    "compare_params": {"period": 50},
                }
            )
        )

    def test_between_with_both_bounds_validates(self):
        validate_group(_group(_rsi_condition(operator="between", value=30, value2=45)))

    def test_bollinger_field_validates(self):
        validate_group(
            _group(
                {
                    "indicator": "bollinger",
                    "params": {"period": 20, "std_dev": 2},
                    "field": "pct_b",
                    "operator": "<",
                    "value": 0.1,
                }
            )
        )


class SchemaRejectionTests(unittest.TestCase):
    def _assert_rejected(self, group, fragment):
        with self.assertRaises(DslValidationError) as ctx:
            validate_group(group)
        self.assertIn(fragment, str(ctx.exception))

    def test_unknown_field_is_rejected(self):
        self._assert_rejected(_group(_rsi_condition(surprise=1)), "surprise")

    def test_risk_field_injected_into_dsl_is_rejected(self):
        self._assert_rejected(_group(_rsi_condition(stop_loss_pct=50.0)), "stop_loss_pct")
        with self.assertRaises(DslValidationError):
            validate_group({"logic": "AND", "conditions": [_rsi_condition()], "position_size_r": 5})

    def test_params_out_of_bounds_are_rejected(self):
        self._assert_rejected(_group(_rsi_condition(params={"period": 51})), "outside [2, 50]")
        self._assert_rejected(_group(_rsi_condition(params={"period": 1})), "outside [2, 50]")
        self._assert_rejected(
            _group(
                {
                    "indicator": "bollinger",
                    "params": {"period": 20, "std_dev": 4},
                    "field": "upper",
                    "operator": ">",
                    "value": 1,
                }
            ),
            "outside [1, 3]",
        )

    def test_non_integer_period_is_rejected(self):
        self._assert_rejected(_group(_rsi_condition(params={"period": 14.5})), "must be an integer")

    def test_between_without_value2_is_rejected(self):
        self._assert_rejected(_group(_rsi_condition(operator="between", value=30)), "value2")

    def test_between_with_inverted_bounds_is_rejected(self):
        self._assert_rejected(
            _group(_rsi_condition(operator="between", value=45, value2=30)), "value <= value2"
        )

    def test_five_conditions_are_rejected(self):
        group = _group(*[_rsi_condition(value=20 + i) for i in range(5)])
        self._assert_rejected(group, "too long")

    def test_nested_groups_are_rejected(self):
        nested = {
            "logic": "AND",
            "conditions": [
                {"logic": "OR", "conditions": [_rsi_condition()]},
            ],
        }
        with self.assertRaises(DslValidationError):
            validate_group(nested)

    def test_unknown_indicator_is_rejected(self):
        self._assert_rejected(_group({"indicator": "macd", "operator": ">", "value": 0}), "macd")

    def test_value_str_outside_regime_is_rejected(self):
        self._assert_rejected(_group(_rsi_condition(value_str="favorable")), "reserved for regime")

    def test_equality_operator_outside_regime_is_rejected(self):
        self._assert_rejected(_group(_rsi_condition(operator="==", value=25)), "reserved for regime")

    def test_regime_with_numeric_operator_is_rejected(self):
        self._assert_rejected(
            _group({"indicator": "regime", "operator": ">", "value_str": "favorable"}),
            "regime only supports",
        )

    def test_regime_with_unknown_label_is_rejected(self):
        self._assert_rejected(
            _group({"indicator": "regime", "operator": "==", "value_str": "bullish"}), "bullish"
        )

    def test_warmup_beyond_buffer_is_rejected(self):
        # sma(200) + crosses_* needs 202 candles: first valid at 199, plus the
        # current and previous bars.
        group = _group(
            {
                "indicator": "sma",
                "params": {"period": 200},
                "operator": "crosses_above",
                "value": 65000,
            }
        )
        with self.assertRaises(DslValidationError) as ctx:
            validate_group(group, available_candles=200)
        self.assertIn("warm-up", str(ctx.exception))
        validate_group(group, available_candles=202)

    def test_rising_with_value_is_rejected(self):
        self._assert_rejected(_group(_rsi_condition(operator="rising")), "meaningless")

    def test_compare_mode_indicator_with_value_is_rejected(self):
        self._assert_rejected(
            _group(
                _rsi_condition(
                    compare_mode="indicator",
                    compare_indicator="sma",
                    compare_params={"period": 20},
                )
            ),
            "forbids 'value'",
        )

    def test_missing_required_param_is_rejected(self):
        self._assert_rejected(_group({"indicator": "rsi", "operator": "<", "value": 30}), "requires param")

    def test_close_with_params_is_rejected(self):
        self._assert_rejected(
            _group({"indicator": "close", "params": {"period": 5}, "operator": ">", "value": 1}),
            "takes no param",
        )

    def test_all_errors_are_reported_not_just_the_first(self):
        with self.assertRaises(DslValidationError) as ctx:
            validate_strategy_dsl(
                _group(_rsi_condition(params={"period": 99})),
                _group(_rsi_condition(operator="between", value=10), logic="OR"),
            )
        message = str(ctx.exception)
        self.assertIn("entry.", message)
        self.assertIn("exit.", message)


if __name__ == "__main__":
    unittest.main()
