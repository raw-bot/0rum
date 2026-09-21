"""Manual, isolated paper account for short-only forward observation.

This script has no launchd integration and cannot place broker orders. Its
ledger, fills, equity history and lock are separate from the main portfolio.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from orum.paths import STATE_DIR
from orum.portfolio.paper_engine import PaperEngine
from scripts.run_paper_portfolio import cycle_lock, paper_market_provider


ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "config" / "portfolio_shorts_experimental.yaml"
POSITIONS_PATH = STATE_DIR / "paper_short_experiment_positions.json"
FILLS_PATH = STATE_DIR / "paper_short_experiment_fills.jsonl"
EQUITY_PATH = STATE_DIR / "paper_short_experiment_equity.jsonl"
SHADOW_PATH = STATE_DIR / "paper_short_experiment_regime_shadow.jsonl"
DYNAMIC_RISK_SHADOW_PATH = (
    STATE_DIR / "paper_short_experiment_dynamic_risk_shadow.jsonl"
)
LOCK_PATH = STATE_DIR / "paper_short_experiment.lock"


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text()) or {}


def build_engine(provider=paper_market_provider) -> PaperEngine:
    return PaperEngine(
        load_config(),
        candle_provider=provider,
        positions_path=POSITIONS_PATH,
        fills_path=FILLS_PATH,
        equity_path=EQUITY_PATH,
        shadow_regime_path=SHADOW_PATH,
        dynamic_risk_shadow_path=DYNAMIC_RISK_SHADOW_PATH,
    )


def main() -> int:
    args = set(sys.argv[1:])
    if args - {"--dry", "--once"}:
        raise SystemExit("only --dry and --once are supported")
    engine = build_engine(
        provider=(lambda *_args, **_kwargs: [])
        if "--dry" in args else paper_market_provider
    )
    if "--dry" in args:
        print(
            f"short experiment: {CONFIG_PATH} "
            f"strategies={len(engine._strategies)}"
        )
        return 0
    with cycle_lock(LOCK_PATH) as acquired:
        if not acquired:
            print("short experiment skipped: another cycle is running", file=sys.stderr)
            return 0
        summary = engine.run_cycle()
    print(
        f"[{summary['ts']}] short experiment equity "
        f"${summary['equity_usd']:,.2f} open={summary['open_positions']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
