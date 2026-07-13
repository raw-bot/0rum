"""Walk-forward calibrated return forecast and conservative paper entry gate.

Pure computation: no network, files, account state or strategy imports.  Every
training target used for an origin has already closed at that origin.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

HORIZONS = (6, 12, 24)
QUANTILES = (("p10", 0.10), ("p25", 0.25), ("p50", 0.50), ("p75", 0.75), ("p90", 0.90))
HISTORY_24H_LIMIT = 168


def _iso_timestamp(raw_ts) -> str:
    try:
        seconds = float(raw_ts) / (1000 if float(raw_ts) > 10_000_000_000 else 1)
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return str(raw_ts)


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _features(closes: list[float], index: int) -> tuple[float, float] | None:
    if index < 24 or closes[index - 24] <= 0:
        return None
    trend = closes[index] / closes[index - 24] - 1
    returns = [closes[i] / closes[i - 1] - 1 for i in range(index - 23, index + 1)
               if closes[i - 1] > 0]
    if not returns:
        return None
    mean = sum(returns) / len(returns)
    volatility = math.sqrt(sum((value - mean) ** 2 for value in returns) / len(returns))
    return trend, volatility


def _available_rows(closes: list[float], horizon: int, origin: int) -> list[tuple[int, float, float, float]]:
    """Features and realized target whose target candle is closed by origin."""
    rows = []
    for feature_index in range(24, origin - horizon + 1):
        feature = _features(closes, feature_index)
        if feature is None or closes[feature_index] <= 0:
            continue
        target = closes[feature_index + horizon] / closes[feature_index] - 1
        rows.append((feature_index, feature[0], feature[1], target))
    return rows


def _conditional_quantiles(rows: list[tuple[int, float, float, float]], feature: tuple[float, float],
                           neighbour_count: int) -> tuple[dict[str, float], dict[str, float]]:
    if not rows:
        zeros = {name: 0.0 for name, _ in QUANTILES}
        return zeros, zeros
    trend_values = [row[1] for row in rows]
    vol_values = [row[2] for row in rows]
    trend_scale = max(1e-9, math.sqrt(sum((x - sum(trend_values) / len(trend_values)) ** 2
                                         for x in trend_values) / len(trend_values)))
    vol_scale = max(1e-9, math.sqrt(sum((x - sum(vol_values) / len(vol_values)) ** 2
                                       for x in vol_values) / len(vol_values)))
    ranked = sorted(rows, key=lambda row: ((row[1] - feature[0]) / trend_scale) ** 2
                                          + ((row[2] - feature[1]) / vol_scale) ** 2)
    selected = [row[3] for row in ranked[:min(neighbour_count, len(ranked))]]
    all_targets = [row[3] for row in rows]
    return (
        {name: _quantile(selected, probability) for name, probability in QUANTILES},
        {name: _quantile(all_targets, probability) for name, probability in QUANTILES},
    )


def _pinball(actual: float, predicted: float, probability: float) -> float:
    error = actual - predicted
    return max(probability * error, (probability - 1) * error)


def walk_forward_forecast(candles: list[dict], *, min_evaluations: int = 250,
                          neighbour_count: int = 80) -> dict:
    """Return latest quantiles plus honest chronological validation metrics."""
    valid = [candle for candle in candles if float(candle.get("close", 0)) > 0]
    closes = [float(candle["close"]) for candle in valid]
    if len(closes) < 80:
        return {"active": False, "origin_index": max(0, len(closes) - 1), "horizons": {},
                "history_24h": [], "lock_reasons": [f"history {len(closes)} < 80"]}
    origin = len(closes) - 1
    horizons: dict[str, dict] = {}
    history_24h: list[dict] = []
    lock_reasons: list[str] = []
    for horizon in HORIZONS:
        current_rows = _available_rows(closes, horizon, origin)
        current_feature = _features(closes, origin)
        current, _ = _conditional_quantiles(current_rows, current_feature, neighbour_count)
        evaluations = []
        first_origin = max(24 + horizon + 40, origin - max(min_evaluations + 80, 330) - horizon)
        for eval_origin in range(first_origin, origin - horizon + 1):
            rows = _available_rows(closes, horizon, eval_origin)
            feature = _features(closes, eval_origin)
            if len(rows) < 40 or feature is None:
                continue
            predicted, baseline = _conditional_quantiles(rows, feature, neighbour_count)
            target_index = eval_origin + horizon
            actual = closes[target_index] / closes[eval_origin] - 1
            evaluations.append((predicted, baseline, actual))
            if horizon == 24:
                origin_price = closes[eval_origin]
                median_return = predicted["p50"]
                history_24h.append({
                    "origin_ts": _iso_timestamp(valid[eval_origin].get("ts")),
                    "target_ts": _iso_timestamp(valid[target_index].get("ts")),
                    "origin_price": origin_price,
                    "predicted_price": origin_price * (1 + median_return),
                    "actual_price": closes[target_index],
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
        metrics = {
            "samples": samples,
            "coverage_p10_p90": round(coverage, 6),
            "direction_accuracy": round(direction, 6),
            "pinball_loss": round(model_loss / max(1, samples * len(QUANTILES)), 9),
            "pinball_skill_vs_baseline": round(skill, 6),
            "chronology_verified": True,
            "latest_training_target_index": current_rows[-1][0] + horizon if current_rows else -1,
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
        "origin_price": closes[origin],
        "horizons": horizons,
        "history_24h": history_24h[-HISTORY_24H_LIMIT:],
        "lock_reasons": lock_reasons,
        "method": "walk_forward_nearest_regime",
    }


def decide_forecast_gate(report: dict, *, atr_risk_fraction: float) -> dict:
    """Translate a qualified forecast into a bounded paper-entry decision."""
    if not report.get("active"):
        reasons = report.get("lock_reasons") or ["calibration lock"]
        return {"action": "locked", "multiplier": 1.0, "influenced": False, "reason": "; ".join(reasons)}
    h12 = report["horizons"]["12"]
    h24 = report["horizons"]["24"]
    q12, q24 = h12["quantiles"], h24["quantiles"]
    if (q12["p50"] <= 0 and q24["p50"] <= 0) or q24["p90"] <= 0:
        return {"action": "veto", "multiplier": 0.0, "influenced": True,
                "reason": "qualified distribution opposes a new long"}
    if q24["p10"] < -abs(atr_risk_fraction):
        return {"action": "shrink", "multiplier": 0.5, "influenced": True,
                "reason": "P10 downside exceeds strategy ATR risk"}
    if (q12["p25"] > 0 and q24["p25"] > 0
            and h12["metrics"]["direction_accuracy"] >= 0.55
            and h24["metrics"]["direction_accuracy"] >= 0.55):
        return {"action": "boost", "multiplier": 1.15, "influenced": True,
                "reason": "positive P25 at 12h/24h with qualified direction skill"}
    return {"action": "pass", "multiplier": 1.0, "influenced": False,
            "reason": "qualified forecast does not justify an adjustment"}
