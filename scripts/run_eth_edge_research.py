"""Fetch, persist, and report the frozen ETH/EUR edge study.

This script is research-only. It never imports the paper broker and never edits
portfolio configuration or trading state.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt

from orum.paths import STATE_DIR
from orum.portfolio.ccxt_provider import CcxtClosedCandleProvider
from orum.research.coinbase_history import DAY_MS, fetch_daily_history
from scripts.research_eth_edge import build_research_report

SYMBOLS = ("BTC/EUR", "ETH/EUR")
START_MS = int(datetime(2015, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def daily_gap_count(candles: list[dict]) -> int:
    return sum(
        max(0, (int(current["ts"]) - int(previous["ts"])) // DAY_MS - 1)
        for previous, current in zip(candles, candles[1:])
    )


def _atomic_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def write_research_artifacts(
    cache_dir: Path | str,
    datasets: dict[str, dict[str, list[dict]]],
    report: dict,
) -> list[Path]:
    directory = Path(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for venue in ("coinbase", "kraken"):
        for symbol in SYMBOLS:
            path = directory / f"{venue}_{symbol.lower().replace('/', '_')}_1d.json"
            _atomic_json(path, datasets[venue][symbol])
            paths.append(path)
    report_path = directory / "eth_edge_report.json"
    _atomic_json(report_path, report)
    paths.append(report_path)
    return paths


def run_public_research(*, now_ms: int | None = None) -> tuple[dict, dict]:
    current_ms = time.time_ns() // 1_000_000 if now_ms is None else int(now_ms)
    end_ms = current_ms // DAY_MS * DAY_MS
    coinbase_exchange = ccxt.coinbaseexchange({"enableRateLimit": True})
    coinbase = {
        symbol: fetch_daily_history(
            coinbase_exchange,
            symbol,
            start_ms=START_MS,
            end_ms=end_ms,
            now_ms=current_ms,
        )
        for symbol in SYMBOLS
    }
    kraken_provider = CcxtClosedCandleProvider(
        {"kraken": ccxt.kraken({"enableRateLimit": True})},
        now_ms=lambda: current_ms,
    )
    kraken = {symbol: kraken_provider("kraken", symbol, "1d", 720) for symbol in SYMBOLS}
    datasets = {"coinbase": coinbase, "kraken": kraken}
    report = build_research_report(
        coinbase,
        kraken,
        retrieved_at=datetime.fromtimestamp(current_ms / 1000, timezone.utc).isoformat(timespec="seconds"),
    )
    report["sources"] = {
        venue: {
            symbol: {
                "bars": len(candles),
                "missing_daily_buckets": daily_gap_count(candles),
                "first_timestamp": int(candles[0]["ts"]) if candles else None,
                "last_timestamp": int(candles[-1]["ts"]) if candles else None,
            }
            for symbol, candles in venue_data.items()
        }
        for venue, venue_data in datasets.items()
    }
    return datasets, report


def main() -> int:
    datasets, report = run_public_research()
    output_dir = STATE_DIR / "data_cache"
    paths = write_research_artifacts(output_dir, datasets, report)
    print(json.dumps({"artifacts": [str(path) for path in paths], "report": report}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
