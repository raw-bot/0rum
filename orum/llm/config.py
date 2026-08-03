"""Typed configuration for the opt-in LLM trading laboratory."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import Enum


class ConfigError(ValueError):
    """Raised when LLM laboratory configuration is unsafe or malformed."""


class LlmMode(str, Enum):
    OFF = "off"
    OBSERVER = "observer"
    SHADOW = "shadow"
    PAPER_ASSISTED = "paper_assisted"
    PAPER_AUTONOMOUS = "paper_autonomous"


@dataclass(frozen=True, slots=True)
class LlmTradingConfig:
    mode: LlmMode = LlmMode.OFF
    provider: str = "nvidia"
    model: str = "nvidia/nemotron-3-ultra-550b-a55b"
    analyst_interval_minutes: int = 60
    decision_timeframe: str = "15m"
    request_timeout_seconds: float = 60.0
    max_parse_retries: int = 1
    max_completion_tokens: int = 4096
    paper_min_leverage: float = 1.0
    paper_max_leverage: float = 40.0
    paper_starting_balance_usd: float = 10_000.0
    paper_fee_rate: float = 0.0005
    paper_maintenance_margin_rate: float = 0.005
    allow_stop_beyond_liquidation: bool = False
    jurisdiction_profile: str = "fr_retail"
    max_retrieved_lessons: int = 5

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "LlmTradingConfig":
        if not isinstance(value, Mapping):
            raise ConfigError("llm_trading config must be a mapping")
        known = {field.name for field in fields(cls)}
        unknown = sorted(set(value) - known)
        if unknown:
            raise ConfigError(f"unknown_option: {', '.join(unknown)}")

        try:
            mode = LlmMode(str(value.get("mode", "off")))
        except ValueError as exc:
            raise ConfigError(f"mode is invalid: {value.get('mode')!r}") from exc

        try:
            allow_destructive = value.get("allow_stop_beyond_liquidation", False)
            if not isinstance(allow_destructive, bool):
                raise ConfigError("allow_stop_beyond_liquidation must be boolean")
            config = cls(
                mode=mode,
                provider=str(value.get("provider", "nvidia")).strip(),
                model=str(value.get("model", "nvidia/nemotron-3-ultra-550b-a55b")).strip(),
                analyst_interval_minutes=int(value.get("analyst_interval_minutes", 60)),
                decision_timeframe=str(value.get("decision_timeframe", "15m")).strip(),
                request_timeout_seconds=float(value.get("request_timeout_seconds", 60)),
                max_parse_retries=int(value.get("max_parse_retries", 1)),
                max_completion_tokens=int(value.get("max_completion_tokens", 4096)),
                paper_min_leverage=float(value.get("paper_min_leverage", 1)),
                paper_max_leverage=float(value.get("paper_max_leverage", 40)),
                paper_starting_balance_usd=float(value.get("paper_starting_balance_usd", 10_000)),
                paper_fee_rate=float(value.get("paper_fee_rate", 0.0005)),
                paper_maintenance_margin_rate=float(value.get("paper_maintenance_margin_rate", 0.005)),
                allow_stop_beyond_liquidation=allow_destructive,
                jurisdiction_profile=str(value.get("jurisdiction_profile", "fr_retail")).strip(),
                max_retrieved_lessons=int(value.get("max_retrieved_lessons", 5)),
            )
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"invalid llm_trading config: {exc}") from exc
        config._validate()
        return config

    def _validate(self) -> None:
        if not self.provider:
            raise ConfigError("provider must not be empty")
        if not self.model:
            raise ConfigError("model must not be empty")
        if self.analyst_interval_minutes <= 0:
            raise ConfigError("analyst_interval_minutes must be positive")
        if not self.decision_timeframe:
            raise ConfigError("decision_timeframe must not be empty")
        if not math.isfinite(self.request_timeout_seconds) or self.request_timeout_seconds <= 0:
            raise ConfigError("request_timeout_seconds must be finite and positive")
        if self.max_parse_retries < 0:
            raise ConfigError("max_parse_retries must be non-negative")
        if not 1 <= self.max_completion_tokens <= 8192:
            raise ConfigError("max_completion_tokens must be between 1 and 8192")
        if not math.isfinite(self.paper_min_leverage) or self.paper_min_leverage < 1:
            raise ConfigError("paper_min_leverage must be finite and at least 1")
        if (
            not math.isfinite(self.paper_max_leverage)
            or self.paper_max_leverage < self.paper_min_leverage
            or self.paper_max_leverage > 40
        ):
            raise ConfigError(
                "paper_max_leverage must be finite, at least paper_min_leverage, and at most 40"
            )
        if not math.isfinite(self.paper_starting_balance_usd) or self.paper_starting_balance_usd <= 0:
            raise ConfigError("paper_starting_balance_usd must be finite and positive")
        if not math.isfinite(self.paper_fee_rate) or self.paper_fee_rate < 0:
            raise ConfigError("paper_fee_rate must be finite and non-negative")
        if not math.isfinite(self.paper_maintenance_margin_rate) or not 0 <= self.paper_maintenance_margin_rate < 1:
            raise ConfigError("paper_maintenance_margin_rate must be between 0 and 1")
        if not self.jurisdiction_profile:
            raise ConfigError("jurisdiction_profile must not be empty")
        if self.max_retrieved_lessons < 0:
            raise ConfigError("max_retrieved_lessons must be non-negative")
