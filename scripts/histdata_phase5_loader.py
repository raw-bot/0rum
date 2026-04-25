"""Phase 5 offline HistData loader CLI.

Usage examples:
    python scripts/histdata_phase5_loader.py qa
    python scripts/histdata_phase5_loader.py import --archives data/histdata/xauusd_m1_ascii/archives/*.zip

This script intentionally does not change the runtime market-data provider.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backtesting.historical_loader import (  # noqa: E402
    SUPPORTED_TIMEFRAMES,
    build_candle_records_by_timeframe,
    import_histdata_archives,
    qa_histdata_archive,
)

DEFAULT_ARCHIVE_DIR = PROJECT_ROOT / "data" / "histdata" / "xauusd_m1_ascii" / "archives"


def _resolve_archives(raw_archives: list[str] | None) -> list[Path]:
    if raw_archives:
        archives = [Path(value) for value in raw_archives]
    else:
        archives = sorted(DEFAULT_ARCHIVE_DIR.glob("*.zip"))

    missing = [path for path in archives if not path.exists()]
    if missing:
        raise SystemExit(f"Missing archive(s): {', '.join(str(path) for path in missing)}")
    if not archives:
        raise SystemExit(f"No HistData archives found in {DEFAULT_ARCHIVE_DIR}")
    return archives


def _parse_timeframes(raw_value: str) -> list[str]:
    timeframes = [value.strip().upper() for value in raw_value.split(",") if value.strip()]
    invalid = [tf for tf in timeframes if tf not in SUPPORTED_TIMEFRAMES]
    if invalid:
        raise SystemExit(
            f"Unsupported timeframe(s): {', '.join(invalid)}. "
            f"Supported: {', '.join(SUPPORTED_TIMEFRAMES)}"
        )
    return timeframes


def run_qa(args: argparse.Namespace) -> None:
    archives = _resolve_archives(args.archives)
    print("HistData Phase 5 QA")
    print(f"archives={len(archives)}")

    for archive in archives:
        report = qa_histdata_archive(archive)
        print(
            f"{archive.name}: rows={report.rows} "
            f"first_utc={report.first_timestamp.isoformat() if report.first_timestamp else 'n/a'} "
            f"last_utc={report.last_timestamp.isoformat() if report.last_timestamp else 'n/a'} "
            f"csv={report.csv_name} status={report.status_name or 'n/a'}"
        )

    timeframes = _parse_timeframes(args.timeframes)
    records_by_tf = build_candle_records_by_timeframe(archives, timeframes=timeframes)
    print("resampled_counts:")
    for timeframe in timeframes:
        records = records_by_tf[timeframe]
        first = records[0].timestamp.isoformat() if records else "n/a"
        last = records[-1].timestamp.isoformat() if records else "n/a"
        print(f"{timeframe}: rows={len(records)} first_utc={first} last_utc={last}")


async def run_import(args: argparse.Namespace) -> None:
    archives = _resolve_archives(args.archives)
    timeframes = _parse_timeframes(args.timeframes)
    inserted = await import_histdata_archives(
        archives,
        timeframes=timeframes,
        batch_size=args.batch_size,
    )
    print("HistData Phase 5 import complete")
    for timeframe in timeframes:
        print(f"{timeframe}: inserted={inserted.get(timeframe, 0)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 5 offline HistData loader.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    qa_parser = subparsers.add_parser("qa", help="Read-only archive QA and resample counts.")
    qa_parser.add_argument("--archives", nargs="*", help="Specific ZIP archives to inspect.")
    qa_parser.add_argument(
        "--timeframes",
        default=",".join(SUPPORTED_TIMEFRAMES),
        help="Comma-separated resample targets. Default: M15,H1,H4,D1.",
    )

    import_parser = subparsers.add_parser("import", help="Import candles into PostgreSQL.")
    import_parser.add_argument("--archives", nargs="*", help="Specific ZIP archives to import.")
    import_parser.add_argument(
        "--timeframes",
        default=",".join(SUPPORTED_TIMEFRAMES),
        help="Comma-separated import targets. Default: M15,H1,H4,D1.",
    )
    import_parser.add_argument("--batch-size", type=int, default=5000)

    args = parser.parse_args()
    if args.command == "qa":
        run_qa(args)
    elif args.command == "import":
        asyncio.run(run_import(args))


if __name__ == "__main__":
    main()
