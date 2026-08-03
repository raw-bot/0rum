"""Shadow tracker: the OPPOSITE of every closed LLM trading-lab decision,
zero capital, zero interaction with the LLM lab or the native portfolio.

Backtest on the first 25 (reference) / 28 (evolving) LLM trades (2026-07-30)
showed the model's own calls lose to fading them: following summed to
-53.37%/-38.72%, fading the same trades summed to +35.05%/+16.51%. This
tracks that counterfactual forward, compounding it as a real equity curve,
to see whether the edge holds over more trades before it graduates to any
capital (same shadow-before-real convention as portfolio_shadow.py).

It reuses `opposite_net_return_on_margin`, already computed per closed
outcome by the LLM lab's own tested derivatives-P&L engine (leverage, fees,
pessimistic same-bar ordering) -- this script does no independent price or
P&L math, so it cannot drift from the lab's own accounting.

Usage:
  uv run python scripts/llm_fade_shadow.py --once     # one pass (launchd/cron)
  uv run python scripts/llm_fade_shadow.py --loop     # hourly loop (nohup)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(REPO, "state", "llm_outcomes.jsonl")
OUT = os.path.join(REPO, "state", "llm_fade_shadow.jsonl")
STATE = os.path.join(REPO, "state", "llm_fade_shadow_state.json")


def load_state() -> dict:
    if os.path.exists(STATE):
        with open(STATE) as f:
            return json.load(f)
    return {"equity": {}, "processed_outcome_ids": []}


def log(rec: dict) -> None:
    rec["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(OUT, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"  {rec['lane']:14s} fade close  {rec['symbol']:10s} model={rec['model_side']:5s}->fade={rec['fade_side']:5s} "
          f"ret={rec['opposite_return']:+.4f} equity={rec['equity']:.4f}", flush=True)


def poll_once() -> None:
    if not os.path.exists(SOURCE):
        print("  no llm_outcomes.jsonl yet -- LLM lab hasn't closed a trade", flush=True)
        return

    st = load_state()
    processed = set(st["processed_outcome_ids"])
    equity = st["equity"]
    new_count = 0

    with open(SOURCE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            outcome = rec.get("outcome") or {}
            outcome_id = outcome.get("outcome_id")
            opp_ret = outcome.get("opposite_net_return_on_margin")
            if not outcome_id or outcome_id in processed or opp_ret is None:
                continue
            lane = outcome.get("lane", "unknown")
            equity[lane] = equity.get(lane, 1.0) * (1 + opp_ret)
            model_side = outcome.get("side", "?")
            log({"lane": lane, "outcome_id": outcome_id, "symbol": outcome.get("symbol", "?"),
                 "model_side": model_side, "fade_side": "short" if model_side == "long" else "long",
                 "opposite_return": round(opp_ret, 5), "equity": round(equity[lane], 5),
                 "exit_price": outcome.get("exit_price")})
            processed.add(outcome_id)
            new_count += 1

    st["equity"] = equity
    st["processed_outcome_ids"] = sorted(processed)
    with open(STATE, "w") as f:
        json.dump(st, f)

    if new_count == 0:
        print(f"  no new outcomes ({len(processed)} tracked so far, equity={equity})", flush=True)


def main() -> None:
    if "--loop" in sys.argv:
        while True:
            try:
                poll_once()
            except Exception as e:  # noqa: BLE001 - shadow tracker must never die silently
                print(f"  poll error (retry next hour): {e}", flush=True)
            time.sleep(3600)
    else:
        poll_once()


if __name__ == "__main__":
    main()
