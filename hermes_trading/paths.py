import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# State isolation: tests / dry-runs / ad-hoc injections MUST be able to redirect
# all state I/O away from the live bot. Set HERMES_STATE_DIR to an isolated path
# (e.g. a tempdir) BEFORE importing hermes_trading to keep the live state/ clean.
# Unset => live default, byte-for-byte unchanged behaviour.
STATE_DIR = (
    Path(os.environ["HERMES_STATE_DIR"]).expanduser()
    if os.environ.get("HERMES_STATE_DIR")
    else PROJECT_ROOT / "state"
)
GOAL_PATH = STATE_DIR / "goal.yaml"
STRATEGY_PATH = STATE_DIR / "strategy.yaml"
TRADES_PATH = STATE_DIR / "trades.jsonl"
HYPOTHESES_PATH = STATE_DIR / "hypotheses.jsonl"
HEARTBEAT_PATH = STATE_DIR / "heartbeat.json"
EVENTS_PATH = STATE_DIR / "events.jsonl"
WATCHER_HEARTBEAT_PATH = STATE_DIR / "hermes_watcher.json"
HISTORY_DIR = STATE_DIR / "history"
