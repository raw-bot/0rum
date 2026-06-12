"""Property test: ANY schema-valid DSL group, over ANY candle buffer (even
too short), must evaluate without raising and return a well-formed EvalResult.
"""

import unittest

from hypothesis import given, settings
from hypothesis import strategies as st

from hermes_trading.dsl.evaluator import evaluate
from hermes_trading.dsl.indicators import REGIME_LABELS
from hermes_trading.dsl.schema import validate_group

_PRICES = st.floats(min_value=1.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)
_VALUES = st.floats(min_value=-1_000_000.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)


@st.composite
def candles(draw, min_size=0, max_size=60):
    closes = draw(st.lists(_PRICES, min_size=min_size, max_size=max_size))
    out = []
    for i, close in enumerate(closes):
        spread = draw(st.floats(min_value=0.0, max_value=close / 2))
        out.append(
            {
                "ts": 1700000000000 + i * 60000,
                "open": close,
                "high": close + spread,
                "low": max(close - spread, 0.01),
                "close": close,
                "volume": 1.0,
            }
        )
    return out


@st.composite
def numeric_indicator(draw, prefix=""):
    name = draw(st.sampled_from(["rsi", "sma", "ema", "close", "bollinger", "atr"]))
    spec = {prefix + "indicator": name} if prefix else {"indicator": name}
    params_key = prefix + "params" if prefix else "params"
    field_key = prefix + "field" if prefix else "field"
    if name == "rsi":
        spec[params_key] = {"period": draw(st.integers(2, 50))}
    elif name in ("sma", "ema"):
        spec[params_key] = {"period": draw(st.integers(2, 200))}
    elif name == "atr":
        spec[params_key] = {"period": draw(st.integers(2, 50))}
    elif name == "bollinger":
        spec[params_key] = {
            "period": draw(st.integers(5, 50)),
            "std_dev": draw(st.floats(min_value=1, max_value=3, allow_nan=False)),
        }
        spec[field_key] = draw(st.sampled_from(["upper", "middle", "lower", "pct_b"]))
    return spec


@st.composite
def conditions(draw):
    if draw(st.booleans()) and draw(st.integers(0, 4)) == 0:
        return {
            "indicator": "regime",
            "operator": draw(st.sampled_from(["==", "!="])),
            "value_str": draw(st.sampled_from(list(REGIME_LABELS))),
        }
    condition = draw(numeric_indicator())
    operator = draw(
        st.sampled_from([">", "<", ">=", "<=", "between", "crosses_above", "crosses_below", "rising", "falling"])
    )
    condition["operator"] = operator
    if operator in ("rising", "falling"):
        return condition
    if draw(st.booleans()) and operator not in ("between",):
        rhs = draw(numeric_indicator(prefix="compare_"))
        condition["compare_mode"] = "indicator"
        condition.update(rhs)
        condition["compare_indicator"] = rhs["compare_indicator"]
    else:
        low = draw(_VALUES)
        condition["value"] = low
        if operator == "between":
            condition["value2"] = low + abs(draw(_VALUES))
    return condition


@st.composite
def groups(draw):
    return {
        "logic": draw(st.sampled_from(["AND", "OR"])),
        "conditions": draw(st.lists(conditions(), min_size=1, max_size=4)),
    }


class DslPropertyTests(unittest.TestCase):
    @given(group=groups(), buffer=candles())
    @settings(max_examples=300, deadline=None)
    def test_valid_dsl_never_raises_and_result_is_well_formed(self, group, buffer):
        # Only schema-valid groups are in scope; warm-up may legitimately
        # exceed the random buffer, which must surface as errors, not raises.
        validate_group(group, available_candles=10_000)

        result = evaluate(group, buffer)

        self.assertEqual(set(result), {"triggered", "details", "errors"})
        self.assertIsInstance(result["triggered"], bool)
        self.assertIsInstance(result["errors"], list)
        self.assertEqual(len(result["details"]), len(group["conditions"]))
        for detail in result["details"]:
            self.assertEqual(set(detail), {"condition", "lhs", "rhs", "met", "error"})
            self.assertIsInstance(detail["met"], bool)
            if detail["error"] is not None:
                self.assertIn(detail["error"], result["errors"])
        if result["triggered"] and group["logic"] == "AND":
            self.assertTrue(all(detail["met"] for detail in result["details"]))


if __name__ == "__main__":
    unittest.main()
