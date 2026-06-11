from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = PROJECT_ROOT / "state"
GOAL_PATH = STATE_DIR / "goal.yaml"
STRATEGY_PATH = STATE_DIR / "strategy.yaml"
TRADES_PATH = STATE_DIR / "trades.jsonl"
HYPOTHESES_PATH = STATE_DIR / "hypotheses.jsonl"
HEARTBEAT_PATH = STATE_DIR / "heartbeat.json"
EVENTS_PATH = STATE_DIR / "events.jsonl"
WATCHER_HEARTBEAT_PATH = STATE_DIR / "hermes_watcher.json"
HISTORY_DIR = STATE_DIR / "history"
