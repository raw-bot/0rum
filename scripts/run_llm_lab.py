#!/usr/bin/env python3
"""One-shot entry point for the non-executing LLM observer/shadow laboratory."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from typing import Iterator

import ccxt
import yaml

from orum.dsl.indicators import atr, ema, rsi
from orum.llm.config import ConfigError, LlmMode, LlmTradingConfig
from orum.llm.derivatives import BinanceUsdMPublicProvider
from orum.llm.journal import JsonlJournal
from orum.llm.learning import LearningProcessor
from orum.llm.lessons import LessonBook, MarketCase
from orum.llm.news import GdeltNewsProvider
from orum.llm.openrouter import NvidiaClient, OpenRouterConfigError
from orum.llm.outcomes import OutcomeEvaluator
from orum.llm.paper_runtime import PaperLaneExecutor
from orum.llm.paper_simulator import LlmPaperSimulator
from orum.llm.paper_store import LlmPaperStore
from orum.llm.paper_validator import PaperDecisionValidator
from orum.llm.postmortem import PostMortemService
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
    LLM_OUTCOMES_PATH,
    LLM_POSTMORTEMS_PATH,
    LLM_LESSONS_PATH,
    LLM_MARKET_BRIEFS_PATH,
    LLM_PAPER_FILLS_PATH,
    LLM_REFERENCE_ACCOUNT_PATH,
    LLM_EVOLVING_ACCOUNT_PATH,
    PAPER_POSITIONS_PATH,
)
from orum.portfolio.ccxt_provider import CcxtClosedCandleProvider


FOUNDATION_MODES = {
    LlmMode.OFF, LlmMode.OBSERVER, LlmMode.SHADOW, LlmMode.PAPER_AUTONOMOUS
}
DEFAULT_SYMBOL = "BTC/USDT"
DEFAULT_CANDLE_LIMIT = 240


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _disabled(*args: object, **kwargs: object) -> Any:
    raise LlmRuntimeError("disabled LLM dependency was unexpectedly called")


def load_llm_config(
    path: Path | None,
    mode_override: str | None,
    provider_override: str | None = None,
    model_override: str | None = None,
    timeout_override: float | None = None,
) -> LlmTradingConfig:
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
    if provider_override is not None:
        config = replace(config, provider=provider_override)
    if model_override is not None:
        config = replace(config, model=model_override)
    if timeout_override is not None:
        config = replace(config, request_timeout_seconds=timeout_override)
    config._validate()
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


def _read_json_object(path: Path) -> tuple[dict[str, object] | None, str | None]:
    if not path.exists():
        return None, "file not found"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"unreadable: {type(exc).__name__}"
    if not isinstance(value, Mapping):
        return None, "file is not an object"
    return dict(value), None


def _read_paper_account(
    path: Path = PAPER_POSITIONS_PATH,
    *,
    llm_account_paths: Mapping[str, Path] | None = None,
) -> dict[str, object]:
    native, native_error = _read_json_object(path)
    lane_paths = llm_account_paths or {
        "llm_reference": LLM_REFERENCE_ACCOUNT_PATH,
        "llm_evolving": LLM_EVOLVING_ACCOUNT_PATH,
    }
    lanes: dict[str, object] = {}
    errors: dict[str, str] = {}
    for lane, lane_path in lane_paths.items():
        value, error = _read_json_object(Path(lane_path))
        if value is not None:
            lanes[lane] = value
        elif error is not None:
            errors[lane] = error
    if native_error is not None:
        errors["native"] = native_error
    return {
        "status": "available" if native is not None or lanes else "unavailable",
        "native": native,
        "llm_accounts": lanes,
        "errors": errors,
    }


def _safe_derivatives_payload(value: object) -> dict[str, object]:
    """Normalize optional derivatives evidence or degrade it explicitly."""

    raw = value if isinstance(value, Mapping) else None
    if raw is None:
        to_mapping = getattr(value, "to_mapping", None)
        if callable(to_mapping):
            raw = to_mapping()
    try:
        if not isinstance(raw, Mapping):
            raise TypeError("derivatives payload is not a mapping")
        return json.loads(
            json.dumps(raw, allow_nan=False, ensure_ascii=False, sort_keys=True)
        )
    except (TypeError, ValueError):
        return {
            "status": "unavailable",
            "error": "invalid_derivatives_snapshot",
        }


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
        derivative_snapshot = _safe_derivatives_payload(derivative_snapshot)
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


def _lesson_provider(book: LessonBook, decision_timeframe: str):
    def retrieve(snapshot, brief, limit: int) -> Sequence[Mapping[str, Any]]:
        indicators = snapshot.indicators.get(decision_timeframe, {})
        atr = indicators.get("atr_14") if isinstance(indicators, Mapping) else None
        candles = snapshot.candles.get(decision_timeframe, [])
        price = None if not candles else float(candles[-1]["close"])
        volatility = "unknown" if atr is None or price is None else (
            "high" if float(atr) / price >= 0.02 else "normal"
        )
        derivatives = snapshot.derivatives or {}
        funding = derivatives.get("funding_rate")
        funding_sign = "unknown" if funding is None else (
            "positive" if float(funding) >= 0 else "negative"
        )
        lane_accounts = snapshot.paper_account.get("llm_accounts")
        lane_accounts = lane_accounts if isinstance(lane_accounts, Mapping) else {}
        account = lane_accounts.get("llm_evolving", {})
        has_position = isinstance(account, Mapping) and bool(account.get("positions"))
        case = MarketCase(
            symbol=snapshot.symbol, regime=brief.regime,
            volatility_bucket=volatility, side="unknown", action="unknown",
            funding_sign=funding_sign, oi_change_bucket="unknown",
            narrative_class=brief.narrative_vs_price,
            exposure_bucket="open" if has_position else "low",
        )
        return tuple(item.to_mapping() for item in book.retrieve(case, limit=limit))

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
            paper_executor=None,
        )
    if config.mode not in FOUNDATION_MODES:
        raise LlmRuntimeError(PAPER_MODE_ERROR)
    client_type: type[NvidiaClient]
    if config.provider == "nvidia":
        client_type = NvidiaClient
    else:
        raise ConfigError(f"unsupported LLM provider: {config.provider!r}")

    client = client_type(
        api_key=api_key,
        model=config.model,
        timeout_seconds=config.request_timeout_seconds,
        max_retries=config.max_parse_retries,
        max_completion_tokens=config.max_completion_tokens,
    )
    brief_journal = JsonlJournal(LLM_MARKET_BRIEFS_PATH)
    lesson_book = LessonBook(JsonlJournal(LLM_LESSONS_PATH))
    analyst = MarketAnalyst(client=client, journal=brief_journal)
    trader_options = {
        "client": client,
        "journal": decision_journal,
        "paper_min_leverage": config.paper_min_leverage,
        "paper_max_leverage": config.paper_max_leverage,
        "jurisdiction_profile": config.jurisdiction_profile,
        "decision_timeframe": config.decision_timeframe,
    }
    paper_executor = None
    learning_processor = None
    if config.mode is LlmMode.PAPER_AUTONOMOUS:
        store = LlmPaperStore(
            account_paths={
                "llm_reference": LLM_REFERENCE_ACCOUNT_PATH,
                "llm_evolving": LLM_EVOLVING_ACCOUNT_PATH,
            },
            fills_path=LLM_PAPER_FILLS_PATH,
        )
        for lane in ("llm_reference", "llm_evolving"):
            store.ensure_account(
                lane, starting_balance_usd=config.paper_starting_balance_usd
            )
        paper_executor = PaperLaneExecutor(
            store=store,
            simulator=LlmPaperSimulator(
                fee_rate=config.paper_fee_rate,
                maintenance_margin_rate=config.paper_maintenance_margin_rate,
                allow_stop_beyond_liquidation=config.allow_stop_beyond_liquidation,
            ),
            validator=PaperDecisionValidator(
                maintenance_margin_rate=config.paper_maintenance_margin_rate,
                allow_stop_beyond_liquidation=config.allow_stop_beyond_liquidation,
            ),
            audit_journal=decision_journal,
            starting_balance_usd=config.paper_starting_balance_usd,
        )
        learning_processor = LearningProcessor(
            fills=store.fills,
            decisions=decision_journal,
            briefs=brief_journal,
            outcomes=JsonlJournal(LLM_OUTCOMES_PATH),
            evaluator=OutcomeEvaluator(fee_rate=config.paper_fee_rate),
            postmortem=PostMortemService(
                client=client, journal=JsonlJournal(LLM_POSTMORTEMS_PATH)
            ),
            lessons=lesson_book,
            decision_timeframe=config.decision_timeframe,
        )
    return LlmLabRuntime(
        config=config,
        snapshot_factory=_snapshot_factory(config),
        analyst=analyst,
        reference_trader=ShadowTrader(**trader_options),
        evolving_trader=ShadowTrader(**trader_options),
        decision_journal=decision_journal,
        lesson_provider=_lesson_provider(lesson_book, config.decision_timeframe),
        paper_executor=paper_executor,
        learning_processor=learning_processor,
        account_refresher=_refresh_paper_accounts,
    )


def _refresh_paper_accounts(snapshot):
    return MarketSnapshotBuilder(clock=lambda: snapshot.created_at).build(
        cutoff=snapshot.cutoff,
        symbol=snapshot.symbol,
        candles=snapshot.candles,
        indicators=dict(snapshot.indicators),
        derivatives=dict(snapshot.derivatives),
        macro=dict(snapshot.macro),
        onchain=dict(snapshot.onchain),
        evidence=snapshot.evidence,
        paper_account=_read_paper_account(),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one non-executing LLM observer or shadow cycle."
    )
    parser.add_argument("--mode", choices=[mode.value for mode in LlmMode])
    parser.add_argument("--once", action="store_true", help="run exactly one cycle")
    parser.add_argument("--config", type=Path, help="optional YAML configuration")
    parser.add_argument("--provider", choices=("nvidia",))
    parser.add_argument("--model", help="explicit provider model identifier")
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        help="per-request remote LLM timeout",
    )
    parser.add_argument(
        "--confirm-paper", action="store_true",
        help="explicitly authorize isolated paper-account mutation for this run",
    )
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


@contextmanager
def _paper_run_lock() -> Iterator[None]:
    path = Path(f"{LLM_PAPER_FILLS_PATH}.runtime.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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
        config = load_llm_config(
            args.config,
            args.mode,
            args.provider,
            args.model,
            args.request_timeout_seconds,
        )
    except (ConfigError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if config.mode is LlmMode.PAPER_ASSISTED:
        print(PAPER_MODE_ERROR, file=sys.stderr)
        return 2
    if config.mode is LlmMode.PAPER_AUTONOMOUS and not args.confirm_paper:
        print("paper_autonomous requires --confirm-paper", file=sys.stderr)
        return 2

    environment = os.environ if environ is None else environ
    api_key_name = "NVIDIA_API_KEY" if config.provider == "nvidia" else "OPENROUTER_API_KEY"
    api_key = environment.get(api_key_name)
    if config.mode in {LlmMode.OBSERVER, LlmMode.SHADOW, LlmMode.PAPER_AUTONOMOUS} and not api_key:
        print(f"{api_key_name} is required for a remote LLM call", file=sys.stderr)
        return 2
    try:
        run_lock = _paper_run_lock() if config.mode is LlmMode.PAPER_AUTONOMOUS else nullcontext()
        with run_lock:
            runtime = runtime_factory(config, api_key)
            result = runtime.run_once()
    except (ConfigError, OpenRouterConfigError, LlmRuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except LlmServiceError as exc:
        print(f"LLM cycle failed: {exc}", file=sys.stderr)
        return 1
    _print_result(result)
    for error in result.errors:
        print(error, file=sys.stderr)
    return 1 if result.errors or any(lane.status in {"model_error", "monitor_error"} for lane in result.lanes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
