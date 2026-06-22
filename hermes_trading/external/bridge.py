"""AK MACD bridge — the P1 polling binding (TradingView -> Hermes), shadow-first.

The worker in ``tradingview_external`` mode is a pure RISK supervisor: it never
opens positions. Something must read TradingView's emitted signals and route
them to the orchestrator. That "something" is this bridge — the binding the
backlog called P1, kept out of the worker so the two never deadlock on CDP.

Pipeline per poll:
    tv CLI (data tables) --> extract "payload" cell --> parse (strict)
        --> dedup by source|strategy|symbol|timeframe|event|bar_time
        --> reject intrabar / non-closed / stale
        --> validate against Hermes gates
        --> SHADOW: log the verdict only      (no orchestrator, no trades)
            LIVE : route accepted -> ExternalOrchestrator (paper execution)

SHADOW MODE (default) is strictly observational: it never calls the
orchestrator, never writes trades.jsonl / open_position.json, never touches
goal.yaml. It only appends verdicts to ``state/ak_macd_shadow.jsonl`` so we can
confirm BUY/EXIT detection, zero duplicates, no intrabar, correct symbol/tf —
before any reset or external activation.

The reader is injected (default = the `tv` CLI over CDP) so the whole pipeline
is testable on simulated table responses without TradingView.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import time
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import yaml

from hermes_trading.paths import STATE_DIR, GOAL_PATH, TRADES_PATH
from hermes_trading.external.poller import extract_mirror_payload
from hermes_trading.external.signal import parse_external_signal, ExternalSignalError
from hermes_trading.external.validate import (
    ValidationContext,
    timeframe_to_ms,
    validate_external_signal,
)

# ----------------------------------------------------------------------------
DEFAULT_MCP_CLI = Path.home() / "tradingview-mcp-jackson" / "src" / "cli" / "index.js"
SHADOW_LOG_PATH = STATE_DIR / "ak_macd_shadow.jsonl"
POSITION_PATH = STATE_DIR / "open_position.json"
RESUME_ACK_PATH = STATE_DIR / "manual_resume.ok"

STUDY_NAME = "AK MACD 15m"
STRATEGY_ID = "ak_macd_15m_v1"

# Used ONLY to validate in shadow (so symbol/timeframe/event gates can be
# exercised) — NEVER written to goal.yaml. Activating external for real means
# adding this to goal.yaml deliberately, later.
SHADOW_ALLOWLIST_ENTRY = {
    "id": STRATEGY_ID,
    "symbol": "BTCUSD",
    "engine_asset": "BTC/USDT",
    "timeframe": "15m",
    "events": ["BUY_CANDIDATE", "SELL_CANDIDATE", "EXIT"],
}

# Don't act on a signal older than this many bars (guards against opening a
# trade on a stale lastPayload when the bridge first starts up).
DEFAULT_MAX_STALE_BARS = 2


# ----------------------------------------------------------------------------
def cli_reader(study: str = STUDY_NAME, cli_path: Path = DEFAULT_MCP_CLI,
               timeout: float = 30.0) -> Callable[[], dict]:
    """Reader that shells out to the `tv` CLI to read the emitter's Pine table."""
    def _read() -> dict:
        try:
            out = subprocess.run(
                ["node", str(cli_path), "data", "tables", "-f", study],
                capture_output=True, text=True, timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - the bridge must never crash a loop
            return {"success": False, "error": f"cli invocation failed: {exc}"}
        if out.returncode != 0:
            return {"success": False, "error": out.stderr.strip() or "cli non-zero exit"}
        try:
            return json.loads(out.stdout)
        except json.JSONDecodeError:
            return {"success": False, "error": "cli returned non-JSON"}
    return _read


def _real_goal() -> dict:
    """The live goal.yaml verbatim. LIVE mode uses this: the operator must have
    configured signal_source + the allowlist deliberately — no silent injection."""
    return copy.deepcopy(yaml.safe_load(GOAL_PATH.read_text()) or {})


def _shadow_goal() -> dict:
    """SHADOW-only: inject the AK MACD allowlist entry IN MEMORY so validation can
    exercise the symbol/timeframe/event gates WITHOUT touching goal.yaml."""
    goal = _real_goal()
    allow = goal.setdefault("allowed_external_strategies", [])
    if not any(e.get("id") == STRATEGY_ID for e in allow):
        allow.append(copy.deepcopy(SHADOW_ALLOWLIST_ENTRY))
    goal.setdefault("allowed_external_sources", ["tradingview"])
    return goal


def _load_open_position() -> dict | None:
    if not POSITION_PATH.exists():
        return None
    try:
        return json.loads(POSITION_PATH.read_text())
    except Exception:  # noqa: BLE001
        return None


def _load_trades() -> list[dict]:
    if not TRADES_PATH.exists():
        return []
    out: list[dict] = []
    for line in TRADES_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _default_state() -> dict:
    return {
        "open_position": _load_open_position(),
        "recent_trades": tuple(_load_trades()),
        "resume_ack": RESUME_ACK_PATH.exists(),
        "trading_mode": os.getenv("HERMES_TRADING_MODE", "paper"),
        "price_offline": False,
    }


def _load_seen_keys(log_path: Path) -> set[str]:
    """Restore dedup keys from a prior shadow log so restarts don't re-process."""
    seen: set[str] = set()
    if not log_path.exists():
        return seen
    for line in log_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = rec.get("dedup_key")
        if key:
            seen.add(key)
    return seen


# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class BridgeVerdict:
    action: str                  # no_table|no_signal|malformed|duplicate|stale|rejected|accepted|executed
    detail: str
    event: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    bar_time: int | None = None
    dedup_key: str | None = None
    dedup_hash: str | None = None
    payload: str | None = None


class AkMacdBridge:
    def __init__(
        self,
        *,
        reader: Callable[[], dict],
        shadow: bool = True,
        goal_loader: Callable[[], dict] | None = None,
        state_loader: Callable[[], dict] = _default_state,
        log_path: Path = SHADOW_LOG_PATH,
        now_ms: Callable[[], int] | int | None = None,
        max_stale_bars: int = DEFAULT_MAX_STALE_BARS,
        printer: Callable[[str], None] | None = print,
        orchestrator=None,
    ):
        self.reader = reader
        self.shadow = shadow
        # SHADOW injects the allowlist in-memory; LIVE uses goal.yaml verbatim.
        self.goal_loader = goal_loader or (_shadow_goal if shadow else _real_goal)
        self.state_loader = state_loader
        self.log_path = log_path
        self._now_ms = now_ms
        self.max_stale_bars = max_stale_bars
        self._print = printer or (lambda _m: None)
        self.seen: set[str] = _load_seen_keys(log_path)
        self._orchestrator = orchestrator  # injectable; else built lazily in live

    # -- helpers --------------------------------------------------------------
    def _now(self) -> int:
        if callable(self._now_ms):
            return self._now_ms()
        if isinstance(self._now_ms, int):
            return self._now_ms
        return int(time.time() * 1000)

    def _record(self, v: BridgeVerdict) -> BridgeVerdict:
        rec = {"ts": datetime.now(UTC).isoformat(), "mode": "shadow" if self.shadow else "live", **asdict(v)}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        self._print(f"[{rec['mode']}] {v.action:9s} {v.event or '-':14s} {v.detail}")
        return v

    # -- one poll -------------------------------------------------------------
    def poll_once(self) -> BridgeVerdict:
        response = self.reader()
        raw = extract_mirror_payload(response, study_name=STUDY_NAME)
        if raw is None:
            return self._record(BridgeVerdict("no_table", f"no readable {STUDY_NAME} payload"))
        if raw in ("", "none"):
            return self._record(BridgeVerdict("no_signal", "emitter has no signal yet", payload=raw))

        try:
            sig = parse_external_signal(raw)
        except ExternalSignalError as exc:
            return self._record(BridgeVerdict("malformed", str(exc), payload=raw))

        key = sig.dedup_key()
        base = dict(event=sig.event, symbol=sig.symbol, timeframe=sig.timeframe,
                    bar_time=sig.bar_time, dedup_key=key, dedup_hash=sig.dedup_hash(), payload=raw)

        if key in self.seen:
            return self._record(BridgeVerdict("duplicate", "already processed this bar event", **base))

        # Staleness guard BEFORE marking seen: an old/frozen lastPayload must keep
        # surfacing as "stale" (e.g. emitter not updating), not get masked as
        # "duplicate" on the next poll. So a stale signal is NOT added to seen.
        tf_ms = timeframe_to_ms(sig.timeframe)
        if tf_ms:
            age = self._now() - (sig.bar_time + tf_ms)
            if age > self.max_stale_bars * tf_ms:
                return self._record(BridgeVerdict("stale", f"signal {age // tf_ms} bars old", **base))

        self.seen.add(key)
        goal = self.goal_loader()
        st = self.state_loader()
        ctx = ValidationContext(
            goal=goal,
            open_position=st["open_position"],
            recent_trades=tuple(st["recent_trades"]),
            resume_ack=st["resume_ack"],
            trading_mode=st["trading_mode"],
            price_offline=st["price_offline"],
            now_ms=self._now(),
        )
        result = validate_external_signal(sig, ctx)
        if not result.accepted:
            return self._record(BridgeVerdict("rejected", f"{result.check}: {result.reason}", **base))

        if self.shadow:
            return self._record(BridgeVerdict(
                "accepted", f"WOULD {sig.event} {sig.symbol} @ {sig.price} (shadow: not executed)", **base))

        # LIVE: route to the orchestrator for paper execution.
        outcome = self._live_handle(raw)
        return self._record(BridgeVerdict("executed", f"{outcome.stage}/{outcome.status.value}: {outcome.detail}", **base))

    def _live_handle(self, raw: str):
        if self._orchestrator is None:
            self._orchestrator = self._build_live_orchestrator()
        return self._orchestrator.handle(raw)

    def _build_live_orchestrator(self):
        # Imported lazily so shadow mode never pulls the executor / file state.
        from hermes_trading.external.orchestrator import ExternalOrchestrator
        from hermes_trading.external.ingest import ExternalSignalStore
        from hermes_trading.external.state import LiveStateAccess
        return ExternalOrchestrator(
            goal=self.goal_loader(),
            state=LiveStateAccess(),
            store=ExternalSignalStore(),
        )

    def run(self, *, interval: float, iterations: int | None = None) -> None:
        self._print(
            f"AK MACD bridge starting — mode={'SHADOW' if self.shadow else 'LIVE(paper)'}, "
            f"study={STUDY_NAME!r}, interval={interval}s, log={self.log_path}"
        )
        count = 0
        while iterations is None or count < iterations:
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 - a bad poll must never kill the loop
                self._record(BridgeVerdict("error", f"poll failed: {exc}"))
            count += 1
            if iterations is not None and count >= iterations:
                break
            time.sleep(interval)


# ----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="AK MACD TradingView->Hermes polling bridge")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--shadow", dest="shadow", action="store_true", help="log only (default)")
    mode.add_argument("--live", dest="shadow", action="store_false", help="route accepted signals to paper orchestrator")
    p.set_defaults(shadow=True)
    p.add_argument("--interval", type=float, default=30.0, help="seconds between polls")
    p.add_argument("--once", action="store_true", help="poll a single time and exit")
    p.add_argument("--cli", default=str(DEFAULT_MCP_CLI), help="path to the tv CLI index.js")
    args = p.parse_args(argv)

    bridge = AkMacdBridge(reader=cli_reader(cli_path=Path(args.cli)), shadow=args.shadow)
    bridge.run(interval=args.interval, iterations=1 if args.once else None)


if __name__ == "__main__":
    main()
