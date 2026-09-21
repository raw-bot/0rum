"""Nine virtual research accounts. No broker connection or main-account writes."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_paper_portfolio import cycle_lock, load_config, paper_market_provider
from orum.fsio import atomic_write_json
from orum.paths import STATE_DIR
from orum.portfolio.strategy_accounts import initialize, run_cycle, source_hash

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = STATE_DIR / "strategy_accounts" / "v1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init", action="store_true", help="freeze config once; never reset existing accounts")
    parser.add_argument("--once", action="store_true", help="run one cycle")
    args = parser.parse_args()
    with cycle_lock(EXPERIMENT / "cycle.lock") as acquired:
        if not acquired:
            print("research accounts: cycle already running")
            return 0
        try:
            code_hash = source_hash(ROOT)
            if args.init:
                initialize(EXPERIMENT, load_config(), code_hash, ROOT)
            if args.once:
                result = run_cycle(EXPERIMENT, paper_market_provider, STATE_DIR / "cot_gate.json", code_hash)
                print(json.dumps({k: result[k] for k in ("cycle_id", "status", "method_changed")}))
                return 1 if result["status"] != "ok" else 0
        except Exception as exc:
            path = EXPERIMENT / "summary.json"
            previous = json.loads(path.read_text()) if path.exists() else {}
            atomic_write_json(path, {**previous, "status": "error", "runner_error": f"{type(exc).__name__}: {exc}",
                                     "error_at": datetime.now(timezone.utc).isoformat()})
            raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
