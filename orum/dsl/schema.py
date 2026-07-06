"""Schema and semantic validation for the strategy condition DSL.

Two layers, both mandatory before anything LLM-emitted touches strategy.yaml:

1. jsonschema with additionalProperties: false everywhere — an unknown field
   (e.g. a risk field smuggled into a condition) is a hard rejection.
2. Semantic validation the schema language cannot express: per-indicator
   param bounds, operator/field compatibility, and warm-up feasibility
   against the live candle buffer (dsl.CANDLE_BUFFER).

The mutable schema covers ONLY the entry/exit condition groups. Risk fields
(stop_loss_pct, position_size_r, ...) live in strategy.yaml outside this
schema and are never exposed to the LLM.
"""

from __future__ import annotations

import jsonschema

from orum.dsl import CANDLE_BUFFER
from orum.dsl.indicators import REGIME_LABELS, first_valid_index

DSL_VERSION = 1

INDICATOR_WHITELIST = ("rsi", "sma", "ema", "close", "bollinger", "atr", "regime")
NUMERIC_INDICATORS = tuple(name for name in INDICATOR_WHITELIST if name != "regime")

# Param bounds per indicator: {param: (lower, upper)}. Indicators absent from
# an entry take no params at all.
PARAM_BOUNDS: dict[str, dict[str, tuple[float, float]]] = {
    "rsi": {"period": (2, 50)},
    "sma": {"period": (2, 200)},
    "ema": {"period": (2, 200)},
    "bollinger": {"period": (5, 50), "std_dev": (1, 3)},
    "atr": {"period": (2, 50)},
    "close": {},
    "regime": {},
}
REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "rsi": ("period",),
    "sma": ("period",),
    "ema": ("period",),
    "bollinger": ("period", "std_dev"),
    "atr": ("period",),
    "close": (),
    "regime": (),
}
INTEGER_PARAMS = ("period",)

FIELD_WHITELIST: dict[str, tuple[str, ...]] = {
    "bollinger": ("upper", "middle", "lower", "pct_b"),
}

NUMERIC_OPERATORS = (">", "<", ">=", "<=", "between")
SERIES_OPERATORS = ("crosses_above", "crosses_below", "rising", "falling")
REGIME_OPERATORS = ("==", "!=")
ALL_OPERATORS = NUMERIC_OPERATORS + SERIES_OPERATORS + REGIME_OPERATORS

# Operators reading [-2] need one extra candle of history.
OPERATORS_NEEDING_PREV = ("crosses_above", "crosses_below", "rising", "falling")

MAX_CONDITIONS = 4

_PARAMS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "period": {"type": "number"},
        "std_dev": {"type": "number"},
    },
}

CONDITION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["indicator", "operator"],
    "properties": {
        "indicator": {"enum": list(INDICATOR_WHITELIST)},
        "params": _PARAMS_SCHEMA,
        "field": {"type": "string"},
        "operator": {"enum": list(ALL_OPERATORS)},
        "value": {"type": "number"},
        "value2": {"type": "number"},
        "value_str": {"type": "string"},
        "compare_mode": {"enum": ["value", "indicator"]},
        "compare_indicator": {"enum": list(NUMERIC_INDICATORS)},
        "compare_params": _PARAMS_SCHEMA,
        "compare_field": {"type": "string"},
    },
}

GROUP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["logic", "conditions"],
    "properties": {
        # Flat v1: one level, one logic, no nested groups.
        "logic": {"enum": ["AND", "OR"]},
        "conditions": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_CONDITIONS,
            "items": CONDITION_SCHEMA,
        },
    },
}


class DslValidationError(ValueError):
    """Carries every rejection reason so the mutation log shows them all."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _schema_errors(group: dict) -> list[str]:
    validator = jsonschema.Draft202012Validator(GROUP_SCHEMA)
    return [
        f"schema: {'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in validator.iter_errors(group)
    ]


def _param_errors(label: str, indicator: str, params: dict | None) -> list[str]:
    errors: list[str] = []
    params = params or {}
    bounds = PARAM_BOUNDS[indicator]
    for name in REQUIRED_PARAMS[indicator]:
        if name not in params:
            errors.append(f"{label}: {indicator} requires param {name!r}")
    for name, value in params.items():
        if name not in bounds:
            errors.append(f"{label}: {indicator} takes no param {name!r}")
            continue
        lower, upper = bounds[name]
        if not lower <= value <= upper:
            errors.append(f"{label}: {indicator}.{name}={value} outside [{lower}, {upper}]")
        if name in INTEGER_PARAMS and value != int(value):
            errors.append(f"{label}: {indicator}.{name}={value} must be an integer")
    return errors


def _field_errors(label: str, indicator: str, field: str | None) -> list[str]:
    allowed = FIELD_WHITELIST.get(indicator)
    if allowed is None:
        if field not in (None, "value"):
            return [f"{label}: {indicator} has no named field {field!r}"]
        return []
    if field is None:
        return [f"{label}: {indicator} requires field in {list(allowed)}"]
    if field not in allowed:
        return [f"{label}: {indicator} field {field!r} not in {list(allowed)}"]
    return []


def _condition_warmup(condition: dict) -> int:
    """Candles required to evaluate this condition without NaN."""
    needed = first_valid_index(condition["indicator"], condition.get("params"))
    if condition.get("compare_mode") == "indicator":
        needed = max(needed, first_valid_index(condition["compare_indicator"], condition.get("compare_params")))
    extra_prev = 1 if condition["operator"] in OPERATORS_NEEDING_PREV else 0
    return needed + 1 + extra_prev


def _semantic_condition_errors(index: int, condition: dict, available_candles: int) -> list[str]:
    label = f"conditions[{index}]"
    errors: list[str] = []
    indicator = condition["indicator"]
    operator = condition["operator"]
    compare_mode = condition.get("compare_mode", "value")

    errors += _param_errors(label, indicator, condition.get("params"))
    errors += _field_errors(label, indicator, condition.get("field"))

    if indicator == "regime":
        if operator not in REGIME_OPERATORS:
            errors.append(f"{label}: regime only supports operators {list(REGIME_OPERATORS)}")
        if "value_str" not in condition:
            errors.append(f"{label}: regime requires value_str")
        elif condition["value_str"] not in REGIME_LABELS:
            errors.append(f"{label}: value_str {condition['value_str']!r} not in {list(REGIME_LABELS)}")
        for forbidden in ("value", "value2", "compare_indicator", "compare_params", "compare_field"):
            if forbidden in condition:
                errors.append(f"{label}: regime condition cannot carry {forbidden!r}")
        if compare_mode != "value":
            errors.append(f"{label}: regime cannot use compare_mode indicator")
    else:
        if operator in REGIME_OPERATORS:
            errors.append(f"{label}: operator {operator!r} is reserved for regime")
        if "value_str" in condition:
            errors.append(f"{label}: value_str is reserved for regime")
        if operator in ("rising", "falling"):
            for forbidden in ("value", "value2", "compare_indicator", "compare_params", "compare_field"):
                if forbidden in condition:
                    errors.append(f"{label}: operator {operator!r} compares the series to itself; {forbidden!r} is meaningless")
            if compare_mode != "value":
                errors.append(f"{label}: operator {operator!r} cannot use compare_mode indicator")
        elif compare_mode == "indicator":
            if "compare_indicator" not in condition:
                errors.append(f"{label}: compare_mode indicator requires compare_indicator")
            else:
                errors += _param_errors(label, condition["compare_indicator"], condition.get("compare_params"))
                errors += _field_errors(label, condition["compare_indicator"], condition.get("compare_field"))
            for forbidden in ("value", "value2"):
                if forbidden in condition:
                    errors.append(f"{label}: compare_mode indicator forbids {forbidden!r}")
        else:
            if "value" not in condition:
                errors.append(f"{label}: operator {operator!r} requires value")
            for forbidden in ("compare_indicator", "compare_params", "compare_field"):
                if forbidden in condition:
                    errors.append(f"{label}: {forbidden!r} requires compare_mode: indicator")
            if operator == "between":
                if "value2" not in condition:
                    errors.append(f"{label}: between requires value2")
                elif "value" in condition and condition["value"] > condition["value2"]:
                    errors.append(f"{label}: between requires value <= value2")
            elif "value2" in condition:
                errors.append(f"{label}: value2 is only valid with between")

    if not errors:
        required = _condition_warmup(condition)
        if required > available_candles:
            errors.append(
                f"{label}: needs {required} candles of warm-up but only {available_candles} are available"
            )
    return errors


def validate_group(group: dict, *, available_candles: int = CANDLE_BUFFER) -> None:
    """Validate one condition group; raises DslValidationError with ALL reasons."""
    errors = _schema_errors(group)
    if not errors:
        for index, condition in enumerate(group["conditions"]):
            errors += _semantic_condition_errors(index, condition, available_candles)
    if errors:
        raise DslValidationError(errors)


def validate_strategy_dsl(entry: dict, exit_group: dict, *, available_candles: int = CANDLE_BUFFER) -> None:
    """Validate both mutable groups of a strategy."""
    errors: list[str] = []
    for name, group in (("entry", entry), ("exit", exit_group)):
        try:
            validate_group(group, available_candles=available_candles)
        except DslValidationError as exc:
            errors += [f"{name}.{message}" for message in exc.errors]
    if errors:
        raise DslValidationError(errors)
