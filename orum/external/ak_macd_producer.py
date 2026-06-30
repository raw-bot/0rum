"""Local AK MACD producer — the Python replacement for the TradingView bridge.

Computes the AK MACD candidate ITSELF from a fresh 15m candle buffer
(orum.external.ak_macd) and routes any confirmed candidate through the
EXACT same downstream pipeline as the TV bridge: parse -> dedup -> validate ->
bracket -> PaperExecutor. The bot is its own signal brain; TradingView is only a
visual/audit reference. Runs as a SEPARATE process from the worker.

LONG entries follow the candidate lifecycle (spec:
specs/ak-macd-long-entry-confirmation.md): each new CLOSED 15m bar produces one
lifecycle verdict (candidate_armed / candidate_confirmed / candidate_expired /
rejected_regime / rejected_macd_not_rising / ...) logged to
state/ak_macd_local_shadow.jsonl. Only a `candidate_confirmed` (LONG) or a SHORT
setup carries a payload and is routed.

  SHADOW (default): log the verdict only; never execute.
  LIVE: route the payload to ExternalOrchestrator for paper execution.

The candle fetch is injected (default = Binance public 15m klines) so the whole
pipeline is unit-testable on synthetic candles without the network.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import httpx
import yaml

from orum.events import log_event
from orum.external.ak_macd import (
    ACTION_CONFIRMED,
    AkMacdParams,
    AkMacdVerdict,
    evaluate_ak_macd_verdict,
)
from orum.external.signal import ExternalSignalError, parse_external_signal
from orum.external.validate import ValidationContext, validate_external_signal
from orum.paths import GOAL_PATH, STATE_DIR, STRATEGY_PATH

SHADOW_LOG_PATH = STATE_DIR / "ak_macd_local_shadow.jsonl"
STRATEGY_ID = "ak_macd_15m_v1"
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
DEFAULT_LIMIT = 300
# Both LONG and SHORT now emit via the confirmed-candidate path.
_EMIT_ACTIONS = {ACTION_CONFIRMED}


def _binance_symbol(engine_asset: str) -> str:
    return engine_asset.replace("/", "").upper()


def binance_reader(
    engine_asset: str = "BTC/USDT", *, interval: str = "15m",
    limit: int = DEFAULT_LIMIT, timeout: float = 15.0,
) -> Callable[[], list[dict]]:
    """Fetch closed OHLCV candles from Binance; drops the still-forming last bar."""
    symbol = _binance_symbol(engine_asset)

    def _read() -> list[dict]:
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        with httpx.Client(timeout=timeout) as client:
            response = client.get(BINANCE_KLINES, params=params)
            response.raise_for_status()
            rows = response.json()
        candles = [
            {"ts": int(row[0]), "open": float(row[1]), "high": float(row[2]),
             "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])}
            for row in rows
        ]
        return candles[:-1]

    return _read


def load_ak_macd_params(strategy_path: Path = STRATEGY_PATH) -> AkMacdParams:
    """Defaults from AkMacdParams, overridden by the `ak_macd:` section of
    strategy.yaml if present and valid. Read ONCE at producer start (no hot
    reload). Malformed/out-of-range values fall back to the default."""
    defaults = AkMacdParams()
    try:
        data = yaml.safe_load(strategy_path.read_text()) or {}
        cfg = data.get("ak_macd") or {}
    except Exception:  # noqa: BLE001 - missing/malformed config must never crash the producer
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}

    def _pos_int(key: str, default: int) -> int:
        value = cfg.get(key, default)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else default

    def _bool(key: str, default: bool) -> bool:
        value = cfg.get(key, default)
        return value if isinstance(value, bool) else default

    return replace(
        defaults,
        confirmation_bars=_pos_int("confirmation_bars", defaults.confirmation_bars),
        candidate_window_bars=_pos_int("candidate_window_bars", defaults.candidate_window_bars),
        regime_filter=_bool("regime_filter", defaults.regime_filter),
        require_candle_direction=_bool("require_candle_direction", defaults.require_candle_direction),
    )


@dataclass(frozen=True)
class ProducerVerdict:
    action: str
    detail: str
    event: str | None = None
    side: str | None = None
    bar_time: int | None = None
    payload: dict | None = None
    macd: tuple = ()
    regime: str | None = None
    rolling_return: float | None = None
    remaining_window: int | None = None


def _load_goal() -> dict:
    return yaml.safe_load(GOAL_PATH.read_text()) or {}


def _strategy_symbol(goal: dict, engine_asset: str) -> tuple[str, str]:
    for entry in goal.get("allowed_external_strategies", []):
        if entry.get("id") == STRATEGY_ID:
            return str(entry.get("symbol", "BTCUSD")), str(entry.get("engine_asset", engine_asset))
    return "BTCUSD", engine_asset


class AkMacdProducer:
    def __init__(
        self, *,
        reader: Callable[[], list[dict]] | None = None,
        params: AkMacdParams | None = None,
        shadow: bool = True,
        goal_loader: Callable[[], dict] = _load_goal,
        strategy_path: Path = STRATEGY_PATH,
        log_path: Path = SHADOW_LOG_PATH,
        printer: Callable[[str], None] | None = print,
        orchestrator=None,
    ):
        # Config read ONCE at start (Requirement 7); explicit params win (tests).
        self.params = params or load_ak_macd_params(strategy_path)
        self.shadow = shadow
        self.goal_loader = goal_loader
        goal = goal_loader()
        self.payload_symbol, engine_asset = _strategy_symbol(goal, "BTC/USDT")
        self.reader = reader or binance_reader(engine_asset)
        self.log_path = log_path
        self._print = printer or (lambda _m: None)
        self._orchestrator = orchestrator
        self._last_bar_ts: int | None = None  # log/route each closed bar once

    def _record(self, v: ProducerVerdict) -> ProducerVerdict:
        rec = {
            "ts": datetime.now(UTC).isoformat(),
            "mode": "shadow" if self.shadow else "live",
            **asdict(v),
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        self._print(
            f"[{rec['mode']}] {v.action:24s} macd={v.macd} regime={v.regime} "
            f"rem={v.remaining_window} {v.detail}"
        )
        return v

    def _build_orchestrator(self):
        from orum.external.ingest import ExternalSignalStore
        from orum.external.orchestrator import ExternalOrchestrator
        from orum.external.state import LiveStateAccess
        return ExternalOrchestrator(
            goal=self.goal_loader(), state=LiveStateAccess(), store=ExternalSignalStore(),
        )

    def _verdict_to_producer(self, verdict: AkMacdVerdict, detail: str) -> ProducerVerdict:
        return ProducerVerdict(
            action=verdict.action, detail=detail, event=verdict.event, side=verdict.side,
            bar_time=verdict.bar_time, payload=verdict.payload, macd=verdict.macd,
            regime=verdict.regime, rolling_return=verdict.rolling_return,
            remaining_window=verdict.remaining_window,
        )

    def _check_engine_parity(self, candles: list[dict], verdict: AkMacdVerdict) -> None:
        """Logs (never raises, never affects routing) when AkMacdEngine
        disagrees with the evaluate_ak_macd_verdict result computed above for
        this bar. Proves wrapper equivalence on live data before any cutover
        relies on it -- see orum/strategies/ak_macd.py. Unrelated to this
        producer's own `self.shadow` (route-vs-log-only); this check always
        runs, in both shadow and live producer mode."""
        if not candles:
            return
        from orum.strategies.ak_macd import AkMacdEngine
        from orum.strategies.base import StrategyContext

        try:
            engine = AkMacdEngine.from_params(self.params)
            signal = engine.on_candle(
                candles[-1], StrategyContext(candles=candles, symbol=self.payload_symbol, timeframe="15m")
            )
        except Exception as exc:  # noqa: BLE001 - a shadow check must never affect routing
            log_event("strategy_engine_shadow_error", f"AkMacdEngine raised during shadow check: {exc}", asset=self.payload_symbol)
            return

        legacy = {"BUY_CANDIDATE": "long", "SELL_CANDIDATE": "short"}.get(verdict.event) if verdict.payload is not None else None
        shadow = signal.side.value if signal else None
        if legacy != shadow:
            log_event(
                "strategy_engine_shadow_disagreement",
                f"AkMacdEngine ({shadow}) disagrees with evaluate_ak_macd_verdict ({legacy})",
                asset=self.payload_symbol,
                legacy=legacy,
                shadow=shadow,
            )

    def poll_once(self) -> ProducerVerdict:
        candles = self.reader()
        verdict = evaluate_ak_macd_verdict(
            candles, self.params, symbol=self.payload_symbol, timeframe="15m", strategy=STRATEGY_ID,
        )

        # Log/route each CLOSED bar exactly once; repeated polls of the same bar
        # are quiet (dedup downstream would drop them anyway).
        if verdict.bar_time is not None and verdict.bar_time == self._last_bar_ts:
            return self._verdict_to_producer(verdict, "no new closed bar")
        self._last_bar_ts = verdict.bar_time
        self._check_engine_parity(candles, verdict)

        if verdict.payload is None or verdict.action not in _EMIT_ACTIONS:
            return self._record(self._verdict_to_producer(verdict, verdict.reason or "lifecycle update"))

        # An entry payload (confirmed LONG or SHORT).
        if self.shadow:
            detail = self._shadow_detail(verdict.payload)
            return self._record(self._verdict_to_producer(verdict, detail))

        if self._orchestrator is None:
            self._orchestrator = self._build_orchestrator()
        outcome = self._orchestrator.handle(verdict.payload)
        return self._record(self._verdict_to_producer(verdict, f"{outcome.stage}: {outcome.detail}"))

    def _shadow_detail(self, payload: dict) -> str:
        """In shadow, report whether the payload WOULD pass 0rum' gates."""
        try:
            signal = parse_external_signal(payload)
        except ExternalSignalError as exc:
            return f"shadow malformed: {exc}"
        from orum.external.state import LiveStateAccess
        state = LiveStateAccess()
        ctx = ValidationContext(
            goal=self.goal_loader(), open_position=state.load_position(),
            recent_trades=tuple(state.trade_history()), resume_ack=state.resume_ack(),
            trading_mode=state.trading_mode(), price_offline=state.price_offline(),
            now_ms=state.now_ms(),
        )
        result = validate_external_signal(signal, ctx)
        if result.accepted:
            return f"WOULD {payload['event']} {payload['symbol']} @ {payload['price']} (shadow)"
        return f"shadow would reject: {result.check}: {result.reason}"

    def run(self, *, interval: float, iterations: int | None = None) -> None:
        self._print(
            f"AK MACD local producer — mode={'SHADOW' if self.shadow else 'LIVE(paper)'}, "
            f"symbol={self.payload_symbol}, confirm={self.params.confirmation_bars}, "
            f"window={self.params.candidate_window_bars}, regime_filter={self.params.regime_filter}, "
            f"interval={interval}s, log={self.log_path}"
        )
        count = 0
        while iterations is None or count < iterations:
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 - a producer must never crash its own loop
                log_event("ak_macd_producer_error", str(exc))
                self._print(f"[error] {exc}")
            count += 1
            if iterations is not None and count >= iterations:
                return
            time.sleep(interval)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Local AK MACD signal producer (Python brain).")
    parser.add_argument("--live", action="store_true", help="execute paper trades (default: shadow only)")
    parser.add_argument("--interval", type=float, default=60.0, help="seconds between polls")
    parser.add_argument("--once", action="store_true", help="poll a single time and exit")
    args = parser.parse_args(argv)

    producer = AkMacdProducer(shadow=not args.live)
    producer.run(interval=args.interval, iterations=1 if args.once else None)


if __name__ == "__main__":
    main()
