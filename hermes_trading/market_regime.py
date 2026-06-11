from __future__ import annotations


def rolling_return_regime(closes: list[float], *, lookback: int = 20, threshold_pct: float = 0.003) -> dict:
    """Classify market context without turning the worker into a predictor."""
    if len(closes) < 2:
        return {
            "label": "unknown",
            "method": "rolling_return_classifier",
            "return_pct": 0.0,
            "lookback": 0,
            "reason": "not enough closes",
        }

    window = closes[-lookback:] if len(closes) >= lookback else closes
    first = float(window[0])
    last = float(window[-1])
    return_pct = ((last - first) / first) if first else 0.0

    if return_pct >= threshold_pct:
        label = "favorable"
    elif return_pct <= -threshold_pct:
        label = "unfavorable"
    else:
        label = "neutral"

    return {
        "label": label,
        "method": "rolling_return_classifier",
        "return_pct": return_pct,
        "lookback": len(window),
        "reason": f"{len(window)}-candle rolling return {return_pct * 100:.2f}%",
    }
