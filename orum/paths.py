import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# State isolation: tests / dry-runs / ad-hoc injections MUST be able to redirect
# all state I/O away from the live bot. Set 0RUM_STATE_DIR to an isolated path
# (e.g. a tempdir) BEFORE importing orum to keep the live state/ clean.
# Unset => live default, byte-for-byte unchanged behaviour.
STATE_DIR = (
    Path(os.environ["0RUM_STATE_DIR"]).expanduser()
    if os.environ.get("0RUM_STATE_DIR")
    else PROJECT_ROOT / "state"
)
GOAL_PATH = STATE_DIR / "goal.yaml"
STRATEGY_PATH = STATE_DIR / "strategy.yaml"
TRADES_PATH = STATE_DIR / "trades.jsonl"
HYPOTHESES_PATH = STATE_DIR / "hypotheses.jsonl"
HEARTBEAT_PATH = STATE_DIR / "heartbeat.json"
EVENTS_PATH = STATE_DIR / "events.jsonl"
WATCHER_HEARTBEAT_PATH = STATE_DIR / "orum_watcher.json"
HISTORY_DIR = STATE_DIR / "history"

# COT gate cache: written by the separate updater (scripts/update_cot_gate.py),
# read ONLY by the gold_cot strategy. COT is a weekly, gold-specific dependency;
# keeping it in a cache file (never fetched inside on_candle) is what isolates it
# from the BTC/ETH strategies and from the per-loop hot path.
COT_GATE_PATH = STATE_DIR / "cot_gate.json"

# Unified paper portfolio ledger (single account for every strategy).
PAPER_FILLS_PATH = STATE_DIR / "paper_fills.jsonl"
PAPER_POSITIONS_PATH = STATE_DIR / "paper_positions.json"
PAPER_EQUITY_PATH = STATE_DIR / "paper_equity.jsonl"
FORECAST_GATE_PATH = STATE_DIR / "forecast_gate.json"
FORECAST_AUDIT_PATH = STATE_DIR / "forecast_audit.jsonl"
FORECAST_HISTORY_PATH = STATE_DIR / "forecast_history.jsonl"

# Append-only LLM laboratory journals. These remain under the same redirectable
# state root so tests and ad-hoc experiments cannot write into live bot state.
LLM_MARKET_BRIEFS_PATH = STATE_DIR / "llm_market_briefs.jsonl"
LLM_DECISIONS_PATH = STATE_DIR / "llm_decisions.jsonl"
LLM_OUTCOMES_PATH = STATE_DIR / "llm_outcomes.jsonl"
LLM_LESSONS_PATH = STATE_DIR / "llm_lessons.jsonl"
