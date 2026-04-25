"""Read-only IG demo probe for 0rum.

Usage examples:
    python scripts/ig_demo_probe.py
    python scripts/ig_demo_probe.py --search gold
    python scripts/ig_demo_probe.py --count 3
"""

from __future__ import annotations

import argparse
import asyncio
from pprint import pprint
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_settings
from src.ingestion.ig_client import IGClient


async def _run(
    search_term: str | None,
    count: int,
    from_time: str | None,
    to_time: str | None,
) -> None:
    settings = get_settings()

    async with IGClient(settings=settings) as client:
        session = await client.authenticate()
        print("Authenticated:", True)
        print("Current account:", client.current_account_id)
        print("Session account count:", len(session.get("accounts", [])))

        accounts = await client.get_accounts()
        print("\nAccounts:")
        pprint(accounts)

        if search_term:
            markets = await client.search_markets(search_term)
            print(f"\nMarket search results for {search_term!r}:")
            pprint(markets[:5])

        if settings.ig_xauusd_epic:
            market = await client.get_market(settings.ig_xauusd_epic)
            print(f"\nMarket details for {settings.ig_xauusd_epic}:")
            pprint(
                {
                    "instrument_name": market.get("instrument", {}).get("name"),
                    "instrument_type": market.get("instrument", {}).get("type"),
                    "market_status": market.get("snapshot", {}).get("marketStatus"),
                    "epic": market.get("instrument", {}).get("epic"),
                }
            )

            for timeframe in ("M15", "H1", "H4", "D1"):
                candles = await client.get_candles(
                    "XAUUSD",
                    timeframe,
                    count=count,
                    from_time=from_time,
                    to_time=to_time,
                )
                print(f"\nSample {timeframe} candles ({len(candles)} rows):")
                pprint(candles[: min(len(candles), count)])
        else:
            print("\nIG_XAUUSD_EPIC is empty: skipping market details and candle fetch.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only IG demo probe for 0rum.")
    parser.add_argument(
        "--search",
        dest="search_term",
        help="Optional IG market search term, e.g. 'gold'.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="How many sample candles to fetch when IG_XAUUSD_EPIC is set.",
    )
    parser.add_argument(
        "--from-time",
        help="Optional UTC/ISO start time for historical range probing.",
    )
    parser.add_argument(
        "--to-time",
        help="Optional UTC/ISO end time for historical range probing.",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.search_term, args.count, args.from_time, args.to_time))


if __name__ == "__main__":
    main()
