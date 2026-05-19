"""Fetch public Dukascopy `.bi5` tick data into the local research cache.

Examples:
    ./.venv/bin/python scripts/dukascopy_fetch.py probe --symbol XAUUSD --date 2026-05-18 --hour 9
    ./.venv/bin/python scripts/dukascopy_fetch.py range --symbol XAUUSD --start 2026-05-18T09:00:00Z --hours 1
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.dukascopy import (  # noqa: E402
    DukascopyTickDownloader,
    build_batch_plan,
    build_dukascopy_bi5_url,
    build_qa_report,
    import_dukascopy_ohlcv_cache,
)


DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "research" / "dukascopy"


def parse_utc_datetime(raw: str) -> datetime:
    """Parse ISO datetime text as UTC."""
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def format_plan(plan) -> list[str]:
    """Format a guarded Dukascopy batch plan for CLI output."""
    lines = [
        "Dukascopy batch plan",
        f"symbol={plan.symbol}",
        f"start={plan.start.isoformat()}",
        f"end={plan.end.isoformat()}",
        f"batch_days={plan.batch_days}",
        f"max_days={plan.max_days}",
        f"total_days={plan.total_days:.2f}",
        f"total_expected_hours={plan.total_expected_hours}",
        f"total_cached_raw_files={plan.total_cached_raw_files}",
        f"total_missing_raw_files={plan.total_missing_raw_files}",
        f"dry_run={plan.dry_run}",
    ]
    for index, batch in enumerate(plan.batches, start=1):
        complete_ohlcv = ",".join(batch.complete_ohlcv_timeframes) or "-"
        lines.append(
            " ".join(
                [
                    f"batch={index}",
                    f"start={batch.start.isoformat()}",
                    f"end={batch.end.isoformat()}",
                    f"expected_hours={batch.expected_hours}",
                    f"cached_raw_files={batch.cached_raw_files}",
                    f"missing_raw_files={batch.missing_raw_files}",
                    f"complete_ohlcv={complete_ohlcv}",
                ]
            )
        )
    return lines


def format_qa_report(report) -> list[str]:
    """Format a Dukascopy cache QA report for CLI output."""
    lines = [
        "Dukascopy QA",
        f"symbol={report.symbol}",
        f"start={report.start.isoformat()}",
        f"end={report.end.isoformat()}",
        f"timeframe={report.timeframe}",
        f"tick_rows={report.tick_rows}",
        f"first_timestamp={report.first_timestamp.isoformat() if report.first_timestamp else '-'}",
        f"last_timestamp={report.last_timestamp.isoformat() if report.last_timestamp else '-'}",
        f"invalid_ohlc_count={report.invalid_ohlc_count}",
    ]
    for timeframe, rows in report.candle_rows_by_timeframe.items():
        lines.append(f"candle_rows_{timeframe}={rows}")
    for gap in report.gaps:
        lines.append(
            " ".join(
                [
                    "gap=1",
                    f"start={gap.start.isoformat()}",
                    f"end={gap.end.isoformat()}",
                    f"missing_candles={gap.missing_candles}",
                    f"classification={gap.classification.value}",
                ]
            )
        )
    return lines


async def run_probe(args: argparse.Namespace) -> None:
    """Fetch exactly one hour and print a small QA summary."""
    day = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=UTC)
    hour_start = day.replace(hour=args.hour)
    downloader = DukascopyTickDownloader(
        cache_dir=Path(args.cache_dir),
        price_scale=args.price_scale,
        retries=args.retries,
        timeout_seconds=args.timeout,
    )
    try:
        ticks = await downloader.fetch_hour(args.symbol, hour_start, force=args.force)
    finally:
        await downloader.aclose()

    print("Dukascopy probe")
    print(f"url={build_dukascopy_bi5_url(args.symbol, hour_start)}")
    print(f"symbol={args.symbol.upper()}")
    print(f"hour_start={hour_start.isoformat()}")
    print(f"ticks={len(ticks)}")
    if not ticks.empty:
        first = ticks.index[0].isoformat()
        last = ticks.index[-1].isoformat()
        print(f"first={first}")
        print(f"last={last}")
        print(f"bid_min={ticks['bid'].min():.5f}")
        print(f"bid_max={ticks['bid'].max():.5f}")
        print(f"ask_min={ticks['ask'].min():.5f}")
        print(f"ask_max={ticks['ask'].max():.5f}")


async def run_batch_plan(args: argparse.Namespace) -> None:
    """Print a guarded dry-run batch plan."""
    start = parse_utc_datetime(args.start)
    end = parse_utc_datetime(args.end)
    try:
        plan = build_batch_plan(
            symbol=args.symbol,
            start=start,
            end=end,
            batch_days=args.batch_days,
            max_days=args.max_days,
            cache_dir=Path(args.cache_dir),
            timeframes=tuple(args.timeframes),
            dry_run=True,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    for line in format_plan(plan):
        print(line)


async def run_batch_fetch(args: argparse.Namespace) -> None:
    """Plan and optionally execute guarded batch downloads."""
    if args.max_days is None:
        raise RuntimeError("batch-fetch requires --max-days")

    start = parse_utc_datetime(args.start)
    end = parse_utc_datetime(args.end)
    try:
        plan = build_batch_plan(
            symbol=args.symbol,
            start=start,
            end=end,
            batch_days=args.batch_days,
            max_days=args.max_days,
            cache_dir=Path(args.cache_dir),
            timeframes=tuple(args.timeframes),
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    for line in format_plan(plan):
        print(line)
    if args.dry_run:
        return

    downloader = DukascopyTickDownloader(
        cache_dir=Path(args.cache_dir),
        price_scale=args.price_scale,
        retries=args.retries,
        timeout_seconds=args.timeout,
    )

    progress_callback = None
    if args.progress:
        def progress_callback(index: int, total: int, hour_start: datetime, tick_rows: int) -> None:
            print(f"progress={index}/{total} hour={hour_start.isoformat()} ticks={tick_rows}")

    try:
        for index, batch in enumerate(plan.batches, start=1):
            summary = await downloader.download_range(
                symbol=plan.symbol,
                start=batch.start,
                end=batch.end,
                timeframes=tuple(args.timeframes),
                force=args.force,
                progress_callback=progress_callback,
            )
            print(
                " ".join(
                    [
                        f"batch_fetch={index}/{len(plan.batches)}",
                        f"symbol={summary.symbol}",
                        f"start={summary.start.isoformat()}",
                        f"end={summary.end.isoformat()}",
                        f"raw_files={summary.raw_files}",
                        f"tick_rows={summary.tick_rows}",
                        f"cache_dir={summary.cache_dir}",
                    ]
                )
            )
            for timeframe, path in summary.ohlcv_paths.items():
                print(f"ohlcv_{timeframe}={path}")
    finally:
        await downloader.aclose()


async def run_qa(args: argparse.Namespace) -> None:
    """Print a QA report for cached Dukascopy research data."""
    start = parse_utc_datetime(args.start)
    end = parse_utc_datetime(args.end)
    report = build_qa_report(
        cache_dir=Path(args.cache_dir),
        symbol=args.symbol,
        start=start,
        end=end,
        timeframe=args.timeframe,
    )
    for line in format_qa_report(report):
        print(line)


async def run_import_postgres(args: argparse.Namespace) -> None:
    """Import cached Dukascopy OHLCV rows into the candles table."""
    start = parse_utc_datetime(args.start)
    end = parse_utc_datetime(args.end)
    reports = await import_dukascopy_ohlcv_cache(
        cache_dir=Path(args.cache_dir),
        symbol=args.symbol,
        start=start,
        end=end,
        timeframes=tuple(args.timeframes),
        instrument="XAUUSD",
        dry_run=args.dry_run,
        batch_size=args.batch_size,
    )

    print("Dukascopy Postgres import")
    print(f"symbol={args.symbol.upper()}")
    print("instrument=XAUUSD")
    print(f"dry_run={args.dry_run}")
    for timeframe in args.timeframes:
        report = reports[timeframe]
        print(
            " ".join(
                [
                    f"timeframe={report.timeframe}",
                    f"source_rows={report.source_rows}",
                    f"built_rows={report.built_rows}",
                    f"inserted_rows={report.inserted_rows}",
                ]
            )
        )


async def run_range(args: argparse.Namespace) -> None:
    """Fetch a bounded range and write ticks + OHLCV CSV files."""
    start = parse_utc_datetime(args.start)
    end = start + timedelta(hours=args.hours)
    if args.hours < 1 or args.hours > args.max_hours:
        raise RuntimeError(f"Refusing to fetch {args.hours} hours; allowed range is 1..{args.max_hours}")

    downloader = DukascopyTickDownloader(
        cache_dir=Path(args.cache_dir),
        price_scale=args.price_scale,
        retries=args.retries,
        timeout_seconds=args.timeout,
    )
    progress_callback = None
    if args.progress:
        def progress_callback(index: int, total: int, hour_start: datetime, tick_rows: int) -> None:
            print(f"progress={index}/{total} hour={hour_start.isoformat()} ticks={tick_rows}")

    try:
        summary = await downloader.download_range(
            symbol=args.symbol,
            start=start,
            end=end,
            timeframes=tuple(args.timeframes),
            force=args.force,
            progress_callback=progress_callback,
        )
    finally:
        await downloader.aclose()

    print("Dukascopy range download")
    print(f"symbol={summary.symbol}")
    print(f"start={summary.start.isoformat()}")
    print(f"end={summary.end.isoformat()}")
    print(f"raw_files={summary.raw_files}")
    print(f"tick_rows={summary.tick_rows}")
    print(f"cache_dir={summary.cache_dir}")
    for timeframe, path in summary.ohlcv_paths.items():
        print(f"ohlcv_{timeframe}={path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dukascopy public .bi5 research fetcher.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="Download and parse exactly one hourly file.")
    probe.add_argument("--symbol", default="XAUUSD")
    probe.add_argument("--date", required=True, help="UTC date YYYY-MM-DD.")
    probe.add_argument("--hour", type=int, required=True, help="UTC hour 0-23.")
    probe.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    probe.add_argument("--price-scale", type=int)
    probe.add_argument("--retries", type=int, default=2)
    probe.add_argument("--timeout", type=float, default=60.0)
    probe.add_argument("--force", action="store_true")

    range_parser = subparsers.add_parser("range", help="Download a bounded range and aggregate OHLCV.")
    range_parser.add_argument("--symbol", default="XAUUSD")
    range_parser.add_argument("--start", required=True, help="UTC ISO datetime, e.g. 2026-05-18T09:00:00Z.")
    range_parser.add_argument("--hours", type=int, default=1)
    range_parser.add_argument("--max-hours", type=int, default=24)
    range_parser.add_argument("--timeframes", nargs="+", default=["M15", "H1", "H4", "D1"])
    range_parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    range_parser.add_argument("--price-scale", type=int)
    range_parser.add_argument("--retries", type=int, default=2)
    range_parser.add_argument("--timeout", type=float, default=60.0)
    range_parser.add_argument("--progress", action="store_true")
    range_parser.add_argument("--force", action="store_true")

    batch_plan = subparsers.add_parser("batch-plan", help="Plan guarded Dukascopy batch downloads.")
    batch_plan.add_argument("--symbol", default="XAUUSD")
    batch_plan.add_argument("--start", required=True, help="UTC ISO datetime, e.g. 2026-05-18T09:00:00Z.")
    batch_plan.add_argument("--end", required=True, help="UTC ISO datetime, e.g. 2026-05-19T09:00:00Z.")
    batch_plan.add_argument("--batch-days", type=int, required=True)
    batch_plan.add_argument("--max-days", type=int)
    batch_plan.add_argument("--timeframes", nargs="+", default=["M15", "H1", "H4", "D1"])
    batch_plan.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))

    batch_fetch = subparsers.add_parser("batch-fetch", help="Download guarded Dukascopy batches.")
    batch_fetch.add_argument("--symbol", default="XAUUSD")
    batch_fetch.add_argument("--start", required=True, help="UTC ISO datetime, e.g. 2026-05-18T09:00:00Z.")
    batch_fetch.add_argument("--end", required=True, help="UTC ISO datetime, e.g. 2026-05-19T09:00:00Z.")
    batch_fetch.add_argument("--batch-days", type=int, required=True)
    batch_fetch.add_argument("--max-days", type=int)
    batch_fetch.add_argument("--timeframes", nargs="+", default=["M15", "H1", "H4", "D1"])
    batch_fetch.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    batch_fetch.add_argument("--price-scale", type=int)
    batch_fetch.add_argument("--retries", type=int, default=2)
    batch_fetch.add_argument("--timeout", type=float, default=60.0)
    batch_fetch.add_argument("--progress", action="store_true")
    batch_fetch.add_argument("--force", action="store_true")
    batch_fetch.add_argument("--dry-run", action="store_true")

    qa = subparsers.add_parser("qa", help="Inspect cached Dukascopy ticks and OHLCV.")
    qa.add_argument("--symbol", default="XAUUSD")
    qa.add_argument("--start", required=True, help="UTC ISO datetime, e.g. 2026-05-18T09:00:00Z.")
    qa.add_argument("--end", required=True, help="UTC ISO datetime, e.g. 2026-05-19T09:00:00Z.")
    qa.add_argument("--timeframe", default="M15")
    qa.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))

    import_postgres = subparsers.add_parser(
        "import-postgres",
        help="Import cached Dukascopy OHLCV into PostgreSQL candles.",
    )
    import_postgres.add_argument("--symbol", default="XAUUSD")
    import_postgres.add_argument("--start", required=True, help="UTC ISO datetime, e.g. 2026-05-18T09:00:00Z.")
    import_postgres.add_argument("--end", required=True, help="UTC ISO datetime, e.g. 2026-05-19T09:00:00Z.")
    import_postgres.add_argument("--timeframes", nargs="+", default=["M15", "H1", "H4", "D1"])
    import_postgres.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    import_postgres.add_argument("--batch-size", type=int, default=5000)
    import_postgres.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    try:
        if args.command == "probe":
            asyncio.run(run_probe(args))
        elif args.command == "range":
            asyncio.run(run_range(args))
        elif args.command == "batch-plan":
            asyncio.run(run_batch_plan(args))
        elif args.command == "batch-fetch":
            asyncio.run(run_batch_fetch(args))
        elif args.command == "qa":
            asyncio.run(run_qa(args))
        elif args.command == "import-postgres":
            asyncio.run(run_import_postgres(args))
    except RuntimeError as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
