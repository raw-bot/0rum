#!/usr/bin/env python3
"""One-shot entry point for the non-executing LLM observer/shadow laboratory."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ccxt
import yaml

from orum.dsl.indicators import atr, ema, rsi
from orum.llm.config import ConfigError, LlmMode, LlmTradingConfig
from orum.llm.derivatives import BinanceUsdMPublicProvider
from orum.llm.journal import JsonlJournal
from orum.llm.news import GdeltNewsProvider
from orum.llm.openrouter import OpenRouterClient, OpenRouterConfigError
from orum.llm.runtime import (
    PAPER_MODE_ERROR,
    LaneRunResult,
    LlmLabRuntime,
    LlmRunResult,
    LlmRuntimeError,
)
from orum.llm.services import LlmServiceError, MarketAnalyst, ShadowTrader
from orum.llm.snapshot import MarketSnapshotBuilder
from orum.paths import (
    LLM_DECISIONS_PATH,
    LLM_LESSONS_PATH,
    LLM_MARKET_BRIEFS_PATH,
    PAPER_POSITIONS_PATH,
)
from orum.portfolio.ccxt_provider import CcxtClosedCandleProvider


FOUNDATION_MODES = {LlmMode.OFF, LlmMode.OBSERVER, LlmMode.SHADOW}
DEFAULT_SYMBOL = "BTC/USDT"
DEFAULT_CANDLE_LIMIT = 240


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _disabled(*args: object, **kwargs: object) -> Any:
    raise LlmRuntimeError("disabled LLM dependency was unexpectedly called")


def load_llm_config(path: Path | None, mode_override: str | None) -> LlmTradingConfig:
    raw: Mapping[str, object] = {}
    if path is not None:
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigError(f"cannot read LLM config {path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid YAML in LLM config {path}: {exc}") from exc
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, Mapping):
            raise ConfigError("LLM config file must contain a mapping")
        nested = loaded.get("llm_trading", loaded)
        if not isinstance(nested, Mapping):
            raise ConfigError("llm_trading config must be a mapping")
        raw = nested
    config = LlmTradingConfig.from_mapping(raw)
    if mode_override is not None:
        config = replace(config, mode=LlmMode(mode_override))
    return config


def _last_finite(values: Sequence[float]) -> float | None:
    for value in reversed(values):
        if math.isfinite(value):
            return float(value)
    return None


def _indicators(candles: Mapping[str, list[dict[str, object]]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for timeframe, rows in candles.items():
        result[timeframe] = {
            "ema_20": _last_finite(ema(rows, 20)),
            "ema_50": _last_finite(ema(rows, 50)),
            "rsi_14": _last_finite(rsi(rows, 14)),
            "atr_14": _last_finite(atr(rows, 14)),
            "closed_candle_count": len(rows),
        }
    return result


def _read_paper_account(path: Path = PAPER_POSITIONS_PATH) -> dict[str, object]:
    if not path.exists():
        return {"status": "unavailable", "reason": "paper positions file not found"}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {
            "status": "unavailable",
            "reason": f"paper positions file unreadable: {type(exc).__name__}",
        }
    if not isinstance(value, Mapping):
        return {"status": "unavailable", "reason": "paper positions file is not an object"}
    return {"status": "available", "ledger": dict(value)}


def _snapshot_factory(
    config: LlmTradingConfig,
    *,
    clock: Callable[[], datetime] = _utcnow,
) -> Callable[[], Any]:
    spot_exchange = ccxt.binance({"enableRateLimit": True})
    spot = CcxtClosedCandleProvider(
        {"binance": spot_exchange},
        now_ms=lambda: int(clock().timestamp() * 1000),
    )
    derivatives = BinanceUsdMPublicProvider(clock=clock)
    news = GdeltNewsProvider(clock=clock)
    builder = MarketSnapshotBuilder(clock=clock)
    timeframes = tuple(dict.fromkeys((config.decision_timeframe, "1h", "4h")))

    def create_snapshot():
        cutoff = clock().astimezone(UTC)
        candles = {
            timeframe: spot("binance", DEFAULT_SYMBOL, timeframe, DEFAULT_CANDLE_LIMIT)
            for timeframe in timeframes
        }
        secondary_errors: list[str] = []
        try:
            derivative_snapshot: object = derivatives.fetch("BTC/USDT:USDT")
        except Exception as exc:  # noqa: BLE001 - optional evidence degrades visibly
            derivative_snapshot = {
                "status": "unavailable",
                "error": f"{type(exc).__name__}: {exc}",
            }
        try:
            evidence = news.fetch(cutoff=cutoff, lookback_hours=12, limit=30)
        except Exception as exc:  # noqa: BLE001 - optional evidence degrades visibly
            evidence = ()
            secondary_errors.append(f"gdelt: {type(exc).__name__}: {exc}")
        return builder.build(
            cutoff=cutoff,
            symbol=DEFAULT_SYMBOL,
            candles=candles,
            indicators=_indicators(candles),
            derivatives=derivative_snapshot,
            macro={
                "status": "not_configured",
                "collection_errors": secondary_errors,
            },
            onchain={"status": "not_configured"},
            evidence=evidence,
            paper_account=_read_paper_account(),
        )

    return create_snapshot


def _lesson_provider(journal: JsonlJournal) -> Callable[[int], Sequence[Mapping[str, Any]]]:
    def retrieve(limit: int) -> Sequence[Mapping[str, Any]]:
        if limit <= 0:
            return ()
        records = journal.read(limit=limit)
        lessons: list[Mapping[str, Any]] = []
        for record in records:
            lesson = record.get("lesson", record)
            if isinstance(lesson, Mapping) and isinstance(lesson.get("lesson_id"), str):
                lessons.append(dict(lesson))
        return tuple(lessons)

    return retrieve


def build_runtime(config: LlmTradingConfig, api_key: str | None) -> LlmLabRuntime:
    decision_journal = JsonlJournal(LLM_DECISIONS_PATH)
    if config.mode is LlmMode.OFF:
        return LlmLabRuntime(
            config=config,
            snapshot_factory=_disabled,
            analyst=_disabled,
            reference_trader=_disabled,
            evolving_trader=_disabled,
            decision_journal=decision_journal,
            lesson_provider=_disabled,
        )
    if config.mode not in FOUNDATION_MODES:
        raise LlmRuntimeError(PAPER_MODE_ERROR)
    if config.provider != "openrouter":
        raise ConfigError(f"unsupported LLM provider: {config.provider!r}")

    client = OpenRouterClient(
        api_key=api_key,
        model=config.model,
        timeout_seconds=config.request_timeout_seconds,
        max_retries=config.max_parse_retries,
    )
    brief_journal = JsonlJournal(LLM_MARKET_BRIEFS_PATH)
    analyst = MarketAnalyst(client=client, journal=brief_journal)
    trader_options = {
        "client": client,
        "journal": decision_journal,
        "paper_min_leverage": config.paper_min_leverage,
        "paper_max_leverage": config.paper_max_leverage,
        "jurisdiction_profile": config.jurisdiction_profile,
    }
    return LlmLabRuntime(
        config=config,
        snapshot_factory=_snapshot_factory(config),
        analyst=analyst,
        reference_trader=ShadowTrader(**trader_options),
        evolving_trader=ShadowTrader(**trader_options),
        decision_journal=decision_journal,
        lesson_provider=_lesson_provider(JsonlJournal(LLM_LESSONS_PATH)),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one non-executing LLM observer or shadow cycle."
    )
    parser.add_argument("--mode", choices=[mode.value for mode in LlmMode])
    parser.add_argument("--once", action="store_true", help="run exactly one cycle")
    parser.add_argument("--config", type=Path, help="optional YAML configuration")
    return parser


def _print_result(result: LlmRunResult) -> None:
    print(
        f"mode={result.mode.value} snapshot={result.snapshot_id or '-'} "
        f"brief={result.brief_id or '-'}"
    )
    for lane in result.lanes:
        print(
            f"{lane.lane} status={lane.status} "
            f"decision={lane.decision_id or '-'}"
        )
        if lane.error:
            print(f"{lane.lane} error={lane.error}", file=sys.stderr)


def main(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    runtime_factory: Callable[[LlmTradingConfig, str | None], LlmLabRuntime] = build_runtime,
) -> int:
    args = _parser().parse_args(argv)
    if not args.once:
        print("--once is required in foundation phase", file=sys.stderr)
        return 2
    try:
        config = load_llm_config(args.config, args.mode)
    except (ConfigError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if config.mode in {LlmMode.PAPER_ASSISTED, LlmMode.PAPER_AUTONOMOUS}:
        print(PAPER_MODE_ERROR, file=sys.stderr)
        return 2

    environment = os.environ if environ is None else environ
    api_key = environment.get("OPENROUTER_API_KEY")
    if config.mode in {LlmMode.OBSERVER, LlmMode.SHADOW} and not api_key:
        print("OPENROUTER_API_KEY is required for a remote LLM call", file=sys.stderr)
        return 2
    try:
        runtime = runtime_factory(config, api_key)
        result = runtime.run_once()
    except (ConfigError, OpenRouterConfigError, LlmRuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except LlmServiceError as exc:
        print(f"LLM cycle failed: {exc}", file=sys.stderr)
        return 1
    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
