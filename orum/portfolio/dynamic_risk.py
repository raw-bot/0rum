"""Read-only dynamic-risk research proposals for the paper portfolio.

This module deliberately cannot return an applied risk. It compares the
configured base risk with a fractional-Kelly research proposal and leaves the
caller responsible for using the base risk. Promotion requires a separate ADR
and an untouched prospective holdout.
"""

from __future__ import annotations

import hashlib
import json
from math import isfinite
from pathlib import Path


class DynamicRiskShadow:
    def __init__(
        self,
        artifact: dict | None,
        *,
        artifact_sha256: str | None = None,
        error: str | None = None,
    ) -> None:
        self._artifact = artifact
        self._artifact_sha256 = artifact_sha256
        self._error = error or self._validate(artifact)

    @classmethod
    def from_path(cls, path: Path | str) -> "DynamicRiskShadow":
        try:
            raw = Path(path).read_bytes()
            artifact = json.loads(raw)
        except (OSError, ValueError, TypeError) as exc:
            return cls(None, error=f"artifact_{type(exc).__name__}")
        return cls(
            artifact,
            artifact_sha256=hashlib.sha256(raw).hexdigest(),
        )

    @staticmethod
    def _validate(artifact: dict | None) -> str | None:
        if not isinstance(artifact, dict):
            return "artifact_not_mapping"
        if artifact.get("schema_version") != 1:
            return "artifact_schema_version"
        if artifact.get("status") not in ("research_only", "validated"):
            return "artifact_status"
        if not isinstance(artifact.get("strategy_id"), str):
            return "artifact_strategy_id"
        buckets = artifact.get("buckets")
        if not isinstance(buckets, list) or not buckets:
            return "artifact_buckets"
        for bucket in buckets:
            if not isinstance(bucket, dict) or not isinstance(bucket.get("id"), str):
                return "artifact_bucket_shape"
            try:
                lower = float(bucket["stop_distance_atr_risk_min"])
                upper_raw = bucket.get("stop_distance_atr_risk_max")
                upper = float(upper_raw) if upper_raw is not None else None
                sample_n = int(bucket["sample_n"])
                wins = int(bucket["wins"])
                probability = float(bucket["p_win_research_estimate"])
                payoff = float(bucket["payoff_r_winsorized"])
                fraction = float(bucket["kelly_fraction"])
                cap = float(bucket["max_risk_pct"])
            except (KeyError, TypeError, ValueError):
                return "artifact_bucket_values"
            values = (lower, probability, payoff, fraction, cap)
            if not all(isfinite(value) for value in values):
                return "artifact_bucket_nonfinite"
            if upper is not None and (not isfinite(upper) or upper <= lower):
                return "artifact_bucket_range"
            if lower < 0 or sample_n <= 0 or not 0 <= wins <= sample_n:
                return "artifact_bucket_counts"
            if not 0 <= probability <= 1 or payoff <= 0:
                return "artifact_bucket_estimates"
            if not 0 < fraction <= 1 or not 0 < cap <= 0.05:
                return "artifact_bucket_policy"
        return None

    @staticmethod
    def _fallback(
        *,
        strategy_id: str,
        base_risk_pct: float,
        drawdown_multiplier: float,
        reason: str,
    ) -> dict:
        actual = base_risk_pct * drawdown_multiplier
        return {
            "mode": "fallback",
            "strategy_id": strategy_id,
            "promotion_eligible": False,
            "base_risk_pct": base_risk_pct,
            "drawdown_multiplier": drawdown_multiplier,
            "actual_risk_pct": actual,
            "shadow_risk_pct": actual,
            "fallback_reason": reason,
        }

    def propose(
        self,
        *,
        strategy_id: str,
        base_risk_pct: float,
        drawdown_multiplier: float,
        risk_distance: float,
        atr_risk: float,
    ) -> dict:
        """Return an auditable proposal which cannot alter the applied risk."""
        try:
            base_risk_pct = float(base_risk_pct)
            drawdown_multiplier = float(drawdown_multiplier)
            risk_distance = float(risk_distance)
            atr_risk = float(atr_risk)
        except (TypeError, ValueError):
            return self._fallback(
                strategy_id=strategy_id,
                base_risk_pct=0.0,
                drawdown_multiplier=0.0,
                reason="invalid_runtime_inputs",
            )
        if not all(
            isfinite(value)
            for value in (
                base_risk_pct,
                drawdown_multiplier,
                risk_distance,
                atr_risk,
            )
        ) or base_risk_pct < 0 or not 0 <= drawdown_multiplier <= 1:
            return self._fallback(
                strategy_id=strategy_id,
                base_risk_pct=max(base_risk_pct, 0.0),
                drawdown_multiplier=0.0,
                reason="invalid_runtime_inputs",
            )
        if self._error is not None:
            return self._fallback(
                strategy_id=strategy_id,
                base_risk_pct=base_risk_pct,
                drawdown_multiplier=drawdown_multiplier,
                reason=self._error,
            )
        assert self._artifact is not None
        if strategy_id != self._artifact["strategy_id"]:
            return self._fallback(
                strategy_id=strategy_id,
                base_risk_pct=base_risk_pct,
                drawdown_multiplier=drawdown_multiplier,
                reason="strategy_not_in_artifact",
            )
        if risk_distance <= 0 or atr_risk <= 0:
            return self._fallback(
                strategy_id=strategy_id,
                base_risk_pct=base_risk_pct,
                drawdown_multiplier=drawdown_multiplier,
                reason="invalid_stop_distance",
            )
        ratio = risk_distance / atr_risk
        selected = None
        for bucket in self._artifact["buckets"]:
            lower = float(bucket["stop_distance_atr_risk_min"])
            upper_raw = bucket.get("stop_distance_atr_risk_max")
            upper = float(upper_raw) if upper_raw is not None else None
            if ratio >= lower and (upper is None or ratio < upper):
                selected = bucket
                break
        if selected is None:
            return self._fallback(
                strategy_id=strategy_id,
                base_risk_pct=base_risk_pct,
                drawdown_multiplier=drawdown_multiplier,
                reason="no_matching_bucket",
            )
        probability = float(selected["p_win_research_estimate"])
        payoff = float(selected["payoff_r_winsorized"])
        kelly_full = max(0.0, probability - (1.0 - probability) / payoff)
        pre_drawdown = min(
            kelly_full * float(selected["kelly_fraction"]),
            float(selected["max_risk_pct"]),
        )
        return {
            "mode": "shadow",
            "strategy_id": strategy_id,
            "artifact_sha256": self._artifact_sha256,
            "artifact_status": self._artifact["status"],
            "promotion_eligible": bool(
                self._artifact.get("promotion_eligible", False)
            ),
            "promotion_blocker": self._artifact.get("promotion_blocker"),
            "base_risk_pct": base_risk_pct,
            "drawdown_multiplier": drawdown_multiplier,
            "actual_risk_pct": base_risk_pct * drawdown_multiplier,
            "stop_distance_atr_risk_ratio": ratio,
            "bucket_id": selected["id"],
            "sample_n": int(selected["sample_n"]),
            "wins": int(selected["wins"]),
            "p_win_research_estimate": probability,
            "payoff_r_winsorized": payoff,
            "kelly_full": kelly_full,
            "kelly_fraction": float(selected["kelly_fraction"]),
            "shadow_risk_pct_pre_drawdown": pre_drawdown,
            "shadow_risk_pct": pre_drawdown * drawdown_multiplier,
            "fallback_reason": None,
        }
