from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime

import yaml

from hermes_trading.fsio import atomic_write_json
from hermes_trading.paths import GOAL_PATH, HYPOTHESES_PATH, TRADES_PATH, WATCHER_HEARTBEAT_PATH

ROOT_DIR = WATCHER_HEARTBEAT_PATH.resolve().parents[1]
LOCAL_HERMES_HOME = ROOT_DIR / ".sandbox" / "hermes-local-llm-home"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _load_jsonl(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _latest_reflection_ts(hypotheses: list[dict]) -> str | None:
    return hypotheses[-1].get("ts") if hypotheses else None


def _trades_after(trades: list[dict], ts: str | None) -> list[dict]:
    if not ts:
        return trades
    return [trade for trade in trades if str(trade.get("ts", "")) > ts]


def _write_status(payload: dict) -> None:
    atomic_write_json(WATCHER_HEARTBEAT_PATH, payload)


def watcher_status(*, status: str, reflection_every: int, trades_seen: int, trades_since_reflection: int, detail: str) -> dict:
    return {
        "ts": _now(),
        "status": status,
        "mode": "local_hermes_watcher",
        "reflection_every": reflection_every,
        "trades_seen": trades_seen,
        "trades_since_reflection": trades_since_reflection,
        "detail": detail,
    }


def maybe_reflect_once() -> dict:
    goal = yaml.safe_load(GOAL_PATH.read_text()) or {}
    reflection_every = int(goal.get("reflection_every", 10))
    trades = _load_jsonl(TRADES_PATH)
    hypotheses = _load_jsonl(HYPOTHESES_PATH)
    latest_ts = _latest_reflection_ts(hypotheses)
    pending_trades = _trades_after(trades, latest_ts)

    if len(pending_trades) < reflection_every:
        status = watcher_status(
            status="standby",
            reflection_every=reflection_every,
            trades_seen=len(trades),
            trades_since_reflection=len(pending_trades),
            detail=f"Waiting for {reflection_every - len(pending_trades)} more closed trades before Hermes reflection.",
        )
        _write_status(status)
        return status

    _write_status(
        watcher_status(
            status="reflecting",
            reflection_every=reflection_every,
            trades_seen=len(trades),
            trades_since_reflection=len(pending_trades),
            detail="Reflection threshold reached; running Hermes on latest outcomes.",
        )
    )
    env = os.environ.copy()
    key_path = LOCAL_HERMES_HOME / ".gemini_api_key"
    if key_path.exists() and not env.get("GEMINI_API_KEY"):
        env["GEMINI_API_KEY"] = key_path.read_text().strip()
    env.setdefault("HERMES_REFLECT_HOME", str(LOCAL_HERMES_HOME))
    result = subprocess.run(
        [sys.executable, "-m", "hermes_trading.reflect", "--hermes"],
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=180,
    )
    status = watcher_status(
        status="reflected",
        reflection_every=reflection_every,
        trades_seen=len(trades),
        trades_since_reflection=len(pending_trades),
        detail=json.loads(result.stdout).get("reason", "Hermes reflection completed."),
    )
    _write_status(status)
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Check once and exit.")
    parser.add_argument("--interval-seconds", type=int, default=int(os.getenv("HERMES_WATCH_INTERVAL_SECONDS", "1800")))
    args = parser.parse_args()

    print("Booting Hermes watcher", flush=True)
    while True:
        try:
            status = maybe_reflect_once()
            print(f"hermes watcher {status['status']}: {status['detail']}", flush=True)
        except Exception as exc:  # noqa: BLE001 - watcher must report why the brain is not connected.
            error = watcher_status(
                status="error",
                reflection_every=0,
                trades_seen=0,
                trades_since_reflection=0,
                detail=str(exc),
            )
            _write_status(error)
            print(f"hermes watcher error: {exc}", flush=True)
            if args.once:
                raise
        if args.once:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
