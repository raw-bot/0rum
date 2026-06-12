"""Pure-Python interpreter for validated DSL condition groups.

Contract:
- evaluate() NEVER raises on a schema-valid group; every per-condition
  problem (insufficient warm-up, NaN, bad field) lands in EvalResult
  ["errors"] and in the matching detail entry. Callers write those errors to
  the heartbeat so they are visible at the dashboard — identical behaviour in
  live and backtest (Fincept pitfall #4).
- An errored condition counts as not met: an AND group cannot trigger, an OR
  group can still trigger on its healthy conditions.
- prev is a real read of the bar at [-2], never a copy of the current value
  (Fincept pitfall #3).
"""

from __future__ import annotations

import math

from hermes_trading.dsl import indicators

EvalResult = dict


class _ConditionError(ValueError):
    pass


def _last_two(series: list, label: str) -> tuple:
    if len(series) < 2:
        raise _ConditionError(f"{label}: series too short ({len(series)} values)")
    return series[-2], series[-1]


def _require_number(value, label: str) -> float:
    if not isinstance(value, (int, float)) or math.isnan(value):
        raise _ConditionError(f"{label}: value at this bar is not a number (insufficient warm-up?)")
    return float(value)


def _rhs_series(condition: dict, candles: list[dict], length: int) -> list:
    if condition.get("compare_mode", "value") == "indicator":
        return indicators.series(
            condition["compare_indicator"],
            condition.get("compare_params"),
            condition.get("compare_field"),
            candles,
        )
    return [condition.get("value")] * length


def _evaluate_condition(condition: dict, candles: list[dict]) -> tuple[bool, object, object]:
    """Returns (met, lhs_current, rhs_current); raises _ConditionError on any problem."""
    operator = condition["operator"]
    lhs_series = indicators.series(
        condition["indicator"], condition.get("params"), condition.get("field"), candles
    )

    if condition["indicator"] == "regime":
        label = lhs_series[-1]
        target = condition.get("value_str")
        if target is None:
            raise _ConditionError("regime condition missing value_str")
        if operator == "==":
            return label == target, label, target
        if operator == "!=":
            return label != target, label, target
        raise _ConditionError(f"operator {operator!r} not valid for regime")

    lhs_prev_raw, lhs_curr_raw = _last_two(lhs_series, "lhs")
    lhs_curr = _require_number(lhs_curr_raw, "lhs[-1]")

    if operator in ("rising", "falling"):
        lhs_prev = _require_number(lhs_prev_raw, "lhs[-2]")
        met = lhs_curr > lhs_prev if operator == "rising" else lhs_curr < lhs_prev
        return met, lhs_curr, lhs_prev

    rhs_series = _rhs_series(condition, candles, len(lhs_series))
    rhs_prev_raw, rhs_curr_raw = _last_two(rhs_series, "rhs")
    rhs_curr = _require_number(rhs_curr_raw, "rhs[-1]")

    if operator in ("crosses_above", "crosses_below"):
        lhs_prev = _require_number(lhs_prev_raw, "lhs[-2]")
        rhs_prev = _require_number(rhs_prev_raw, "rhs[-2]")
        if operator == "crosses_above":
            met = lhs_prev <= rhs_prev and lhs_curr > rhs_curr
        else:
            met = lhs_prev >= rhs_prev and lhs_curr < rhs_curr
        return met, lhs_curr, rhs_curr

    if operator == "between":
        value2 = condition.get("value2")
        if value2 is None:
            raise _ConditionError("between requires value2")
        met = rhs_curr <= lhs_curr <= float(value2)
        return met, lhs_curr, (rhs_curr, float(value2))

    comparators = {
        ">": lhs_curr > rhs_curr,
        "<": lhs_curr < rhs_curr,
        ">=": lhs_curr >= rhs_curr,
        "<=": lhs_curr <= rhs_curr,
    }
    if operator not in comparators:
        raise _ConditionError(f"unknown operator {operator!r}")
    return comparators[operator], lhs_curr, rhs_curr


def _format_operand(value) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, tuple):
        return "[" + ", ".join(_format_operand(item) for item in value) + "]"
    return str(value)


def describe_condition(detail: dict) -> str:
    """One-line human summary of a condition evaluation for decisions/heartbeat."""
    condition = detail["condition"]
    name = condition.get("indicator", "?")
    period = (condition.get("params") or {}).get("period")
    label = f"{name}({period})" if period else name
    if condition.get("field"):
        label += f".{condition['field']}"
    if condition.get("compare_mode") == "indicator":
        rhs_period = (condition.get("compare_params") or {}).get("period")
        target = f"{condition.get('compare_indicator')}({rhs_period})" if rhs_period else str(condition.get("compare_indicator"))
    elif "value_str" in condition:
        target = condition["value_str"]
    elif condition.get("operator") == "between":
        target = f"[{_format_operand(condition.get('value'))}, {_format_operand(condition.get('value2'))}]"
    elif "value" in condition:
        target = _format_operand(condition["value"])
    else:
        target = ""
    head = f"{label} {condition.get('operator', '?')} {target}".rstrip()
    if detail["error"]:
        return f"{head} -> error"
    return f"{head} (lhs={_format_operand(detail['lhs'])}) -> {'met' if detail['met'] else 'not met'}"


def summarize(group: dict, result: EvalResult) -> str:
    joiner = " OR " if group.get("logic") == "OR" else " AND "
    return joiner.join(describe_condition(detail) for detail in result["details"]) or "no conditions"


def evaluate(group: dict, candles: list[dict]) -> EvalResult:
    details: list[dict] = []
    errors: list[str] = []
    met_flags: list[bool] = []

    conditions = group.get("conditions") or []
    logic = group.get("logic", "AND")

    for index, condition in enumerate(conditions):
        detail = {"condition": condition, "lhs": None, "rhs": None, "met": False, "error": None}
        try:
            met, lhs, rhs = _evaluate_condition(condition, candles)
            detail.update(met=met, lhs=lhs, rhs=rhs)
        except Exception as exc:  # noqa: BLE001 - every evaluation failure must surface, never propagate.
            message = f"conditions[{index}] ({condition.get('indicator', '?')}): {exc}"
            detail["error"] = message
            errors.append(message)
        details.append(detail)
        met_flags.append(bool(detail["met"]))

    if not met_flags:
        triggered = False
        errors.append("group has no conditions")
    elif logic == "OR":
        triggered = any(met_flags)
    else:
        triggered = all(met_flags)

    return {"triggered": triggered, "details": details, "errors": errors}
