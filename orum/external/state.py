"""Live ``StateAccess`` implementation: reads/writes the same state files the
native engine owns.

This is the production gateway used when the orchestrator runs against real
state. It reuses the native loaders (``_load_open_position``,
``RESUME_ACK_PATH``) so the external path sees exactly what the loop sees, and
writes positions/trades in the same JSONL/JSON shapes. It never mutates the
native engine — only the shared state files.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import yaml

from orum.loop import POSITION_PATH, RESUME_ACK_PATH, _load_open_position
from orum.paths import STRATEGY_PATH, TRADES_PATH


class LiveStateAccess:
    def load_strategy(self) -> dict:
        if not STRATEGY_PATH.exists():
            return {}
        return yaml.safe_load(STRATEGY_PATH.read_text()) or {}

    def load_position(self) -> dict | None:
        return _load_open_position()

    def save_position(self, position: dict) -> None:
        POSITION_PATH.parent.mkdir(parents=True, exist_ok=True)
        POSITION_PATH.write_text(json.dumps(position, sort_keys=True))

    def clear_position(self) -> None:
        POSITION_PATH.unlink(missing_ok=True)

    def append_trade(self, trade: dict) -> None:
        TRADES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TRADES_PATH.open("a") as handle:
            handle.write(json.dumps(trade, sort_keys=True) + "\n")

    def trade_history(self) -> list[dict]:
        if not TRADES_PATH.exists():
            return []
        return [json.loads(line) for line in TRADES_PATH.read_text().splitlines() if line.strip()]

    def resume_ack(self) -> bool:
        return RESUME_ACK_PATH.exists()

    def trading_mode(self) -> str:
        return os.getenv("ORUM_TRADING_MODE", "paper")

    def price_offline(self) -> bool:
        # External signals carry their own (online) price; offline detection is
        # a native-loop concern. Defaults to online here.
        return False

    def now_ms(self) -> int:
        return int(datetime.now(UTC).timestamp() * 1000)
