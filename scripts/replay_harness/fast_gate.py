"""Vectorized re-implementation of orum.portfolio.forecast_gate.walk_forward_forecast.

Purpose: unblock long runtime replays. The production function costs ~3.9s per
evaluation on 900 candles because `_available_rows` recomputes `_features`
for every evaluation origin (~17M calls per call tree). This version
precomputes the feature/target tables once and vectorizes the neighbour
search — same formulas, same walk-forward chronology, same report shape.

Parity standard (tests/test_fast_gate_parity.py): bit-exactness is impossible
because numpy's summation order differs from Python's sequential `sum` at the
1e-16 level. The acceptance bar is DECISION parity on real data:
  * identical `active`, `lock_reasons`, per-horizon `qualified`;
  * identical `decide_forecast_gate` action/multiplier/influenced;
  * quantiles and metrics equal within 1e-9 relative.

This module lives in the harness on purpose: the live worker reloads the
working tree every cycle, so editing orum/portfolio/forecast_gate.py would be
a de facto live deploy. Promotion into orum/ is a separate, explicit step
once the parity suite has run long enough.
"""

from __future__ import annotations

import numpy as np

from orum.portfolio.forecast_gate import (
    HISTORY_24H_LIMIT,
    HORIZONS,
    QUANTILES,
    _iso_timestamp,
    _pinball,
)

_PROBS = np.array([p for _, p in QUANTILES])
_NAMES = [name for name, _ in QUANTILES]


def _quantiles_of(values: np.ndarray) -> dict[str, float]:
    """Same linear interpolation as forecast_gate._quantile."""
    if values.size == 0:
        return {name: 0.0 for name in _NAMES}
    ordered = np.sort(values)
    pos = (ordered.size - 1) * _PROBS
    lo = np.floor(pos).astype(int)
    hi = np.ceil(pos).astype(int)
    w = pos - lo
    out = ordered[lo] * (1 - w) + ordered[hi] * w
    return dict(zip(_NAMES, out.tolist()))


def _feature_tables(closes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """trend[i] and vol[i] for every index (nan below index 24), computed once.
    Formulas mirror forecast_gate._features; closes are pre-filtered > 0 so
    the per-return positivity guard never trips (same as production)."""
    n = closes.size
    trend = np.full(n, np.nan)
    vol = np.full(n, np.nan)
    if n <= 24:
        return trend, vol
    idx = np.arange(24, n)
    trend[idx] = closes[idx] / closes[idx - 24] - 1.0
    rets = closes[1:] / closes[:-1] - 1.0            # ret[i-1] = return of bar i
    win = np.lib.stride_tricks.sliding_window_view(rets, 24)  # win[j] = rets[j:j+24]
    # window for index i covers returns of bars (i-23..i) -> rets[i-24 : i]
    mean = win.mean(axis=1)
    vol[idx] = np.sqrt(np.square(win - mean[:, None]).mean(axis=1))[idx - 24]
    return trend, vol


def _conditional_quantiles_fast(trend: np.ndarray, vol: np.ndarray, target: np.ndarray,
                                k: int, f_trend: float, f_vol: float,
                                neighbour_count: int) -> tuple[dict, dict]:
    """Prefix [24, 24+k) of the row table, matching _available_rows order."""
    if k <= 0:
        zeros = {name: 0.0 for name in _NAMES}
        return zeros, zeros
    t, v, y = trend[24:24 + k], vol[24:24 + k], target[24:24 + k]
    t_scale = max(1e-9, float(np.sqrt(np.square(t - t.mean()).mean())))
    v_scale = max(1e-9, float(np.sqrt(np.square(v - v.mean()).mean())))
    d = np.square((t - f_trend) / t_scale) + np.square((v - f_vol) / v_scale)
    order = np.argsort(d, kind="stable")  # stable = production's tie-breaking
    selected = y[order[:min(neighbour_count, order.size)]]
    return _quantiles_of(selected), _quantiles_of(y)


def walk_forward_forecast_fast(candles: list[dict], *, min_evaluations: int = 250,
                               neighbour_count: int = 80) -> dict:
    """Drop-in replacement for walk_forward_forecast (see parity standard)."""
    valid = [candle for candle in candles if float(candle.get("close", 0)) > 0]
    closes_list = [float(candle["close"]) for candle in valid]
    if len(closes_list) < 80:
        return {"active": False, "origin_index": max(0, len(closes_list) - 1), "horizons": {},
                "history_24h": [], "lock_reasons": [f"history {len(closes_list)} < 80"]}
    closes = np.asarray(closes_list)
    origin = closes.size - 1
    trend, vol = _feature_tables(closes)

    horizons: dict[str, dict] = {}
    history_24h: list[dict] = []
    lock_reasons: list[str] = []
    for horizon in HORIZONS:
        target = np.full(closes.size, np.nan)
        target[:closes.size - horizon] = closes[horizon:] / closes[:-horizon] - 1.0

        def rows_len(o: int) -> int:
            # _available_rows spans feature_index in [24, o - horizon]
            return max(0, (o - horizon) - 24 + 1)

        current, _ = _conditional_quantiles_fast(
            trend, vol, target, rows_len(origin), trend[origin], vol[origin], neighbour_count)

        evaluations: list[tuple[dict, dict, float]] = []
        first_origin = max(24 + horizon + 40, origin - max(min_evaluations + 80, 330) - horizon)
        for eval_origin in range(first_origin, origin - horizon + 1):
            k = rows_len(eval_origin)
            if k < 40 or np.isnan(trend[eval_origin]):
                continue
            predicted, baseline = _conditional_quantiles_fast(
                trend, vol, target, k, trend[eval_origin], vol[eval_origin], neighbour_count)
            target_index = eval_origin + horizon
            actual = closes_list[target_index] / closes_list[eval_origin] - 1
            evaluations.append((predicted, baseline, actual))
            if horizon == 24:
                origin_price = closes_list[eval_origin]
                median_return = predicted["p50"]
                history_24h.append({
                    "origin_ts": _iso_timestamp(valid[eval_origin].get("ts")),
                    "target_ts": _iso_timestamp(valid[target_index].get("ts")),
                    "origin_price": origin_price,
                    "predicted_price": origin_price * (1 + median_return),
                    "actual_price": closes_list[target_index],
                    "median_return": median_return,
                    "median_error": actual - median_return,
                    "source": "walk_forward",
                })
        samples = len(evaluations)
        coverage = (sum(1 for predicted, _, actual in evaluations
                        if predicted["p10"] <= actual <= predicted["p90"]) / samples) if samples else 0.0
        direction = (sum(1 for predicted, _, actual in evaluations
                         if (predicted["p50"] > 0) == (actual > 0)) / samples) if samples else 0.0
        model_loss = sum(_pinball(actual, predicted[name], probability)
                         for predicted, _, actual in evaluations for name, probability in QUANTILES)
        baseline_loss = sum(_pinball(actual, baseline[name], probability)
                            for _, baseline, actual in evaluations for name, probability in QUANTILES)
        denominator = max(baseline_loss, 1e-12)
        skill = 1 - model_loss / denominator
        rows_last = 24 + rows_len(origin) - 1  # feature index of the last row
        metrics = {
            "samples": samples,
            "coverage_p10_p90": round(coverage, 6),
            "direction_accuracy": round(direction, 6),
            "pinball_loss": round(model_loss / max(1, samples * len(QUANTILES)), 9),
            "pinball_skill_vs_baseline": round(skill, 6),
            "chronology_verified": True,
            "latest_training_target_index": rows_last + horizon if rows_len(origin) > 0 else -1,
        }
        qualified = (samples >= min_evaluations and 0.72 <= coverage <= 0.88
                     and direction >= 0.52 and skill > 0)
        horizons[str(horizon)] = {"quantiles": current, "metrics": metrics, "qualified": qualified}
        if horizon in (12, 24) and not qualified:
            lock_reasons.append(
                f"{horizon}h: samples={samples}, coverage={coverage:.3f}, direction={direction:.3f}, skill={skill:.3f}"
            )
    return {
        "active": not lock_reasons,
        "origin_index": origin,
        "origin_ts": _iso_timestamp(valid[-1].get("ts")),
        "origin_price": closes_list[origin],
        "horizons": horizons,
        "history_24h": history_24h[-HISTORY_24H_LIMIT:],
        "lock_reasons": lock_reasons,
        "method": "walk_forward_nearest_regime",
    }
