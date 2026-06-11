from __future__ import annotations

import argparse
import asyncio

import yaml

from hermes_trading.loop import run_loop
from hermes_trading.paths import GOAL_PATH


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", help="Override the asset configured in state/goal.yaml")
    args = parser.parse_args()

    goal = yaml.safe_load(GOAL_PATH.read_text()) or {}
    if args.asset:
        goal["asset"] = args.asset
    asyncio.run(run_loop(goal))


if __name__ == "__main__":
    main()
