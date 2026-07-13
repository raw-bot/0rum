"""Runner for the unified multi-strategy paper portfolio.

Wires the real Binance candle provider (scripts/data_layer) to
orum.portfolio.PaperEngine, driven by state/portfolio.yaml. All paper, no live,
no TradingView. The old mono-asset worker is untouched by this script — bringing
the portfolio up and retiring the worker are deliberately separate steps.

Usage:
  uv run python scripts/run_paper_portfolio.py --dry     # offline: build + list, no fetch
  uv run python scripts/run_paper_portfolio.py --once    # one cycle (launchd/cron)
  uv run python scripts/run_paper_portfolio.py --loop    # 15-minute loop (nohup)
"""

from __future__ import annotations

import os
import sys
import time
import fcntl
from contextlib import contextmanager
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from data_layer import fetch_klines  # noqa: E402
from orum.paths import STATE_DIR  # noqa: E402
from orum.portfolio.paper_engine import PaperEngine  # noqa: E402

PORTFOLIO_PATH = STATE_DIR / "portfolio.yaml"
PAPER_LOCK_PATH = STATE_DIR / "paper_engine.lock"


@contextmanager
def cycle_lock(path=PAPER_LOCK_PATH):
    """Prevent concurrent launchd/manual cycles from writing the same ledger."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_guarded_cycle(engine: PaperEngine) -> dict | None:
    with cycle_lock() as acquired:
        if not acquired:
            print("paper cycle skipped: another cycle is already running", file=sys.stderr)
            return None
        return engine.run_cycle()


def _interval_ms(timeframe: str) -> int:
    unit_ms = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    try:
        return int(timeframe[:-1]) * unit_ms[timeframe[-1]]
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise ValueError(f"unsupported Binance timeframe {timeframe!r}") from exc


def binance_provider(symbol: str, timeframe: str, limit: int) -> list[dict]:
    """`BTC/USDT` -> Binance `BTCUSDT`; `4h`/`1d` are valid Binance intervals.
    cache=False so each cycle sees the latest closed bar.

    Normalizes data_layer's `time` (epoch seconds) to the canonical orum candle
    shape with `ts` in epoch milliseconds — the ak_macd brain reads `int(c["ts"])`
    (orum/external/ak_macd.py) while donchian/gold_cot only touch OHLC."""
    rows = fetch_klines(symbol.replace("/", ""), timeframe, limit + 1, cache=False)
    normalized = [
        {"ts": int(r["time"]) * 1000, "open": r["open"], "high": r["high"],
         "low": r["low"], "close": r["close"], "volume": r["volume"]}
        for r in rows
    ]
    now_ms = int(time.time() * 1000)
    interval_ms = _interval_ms(timeframe)
    return [row for row in normalized if row["ts"] + interval_ms <= now_ms][-limit:]


def load_config() -> dict:
    return yaml.safe_load(PORTFOLIO_PATH.read_text()) or {}


def build_engine(provider=binance_provider) -> PaperEngine:
    return PaperEngine(load_config(), candle_provider=provider)


def _print_summary(summary: dict) -> None:
    print(f"[{summary['ts']}] equity ${summary['equity_usd']:,.2f} "
          f"(cash ${summary['balance_usd']:,.2f})  open={summary['open_positions']}")
    for sid, intent in summary["intents"].items():
        print(f"    {sid:<18} {intent}")
    for sid, err in summary.get("errors", {}).items():
        print(f"    {sid:<18} ERROR: {err}", file=sys.stderr)


def main() -> int:
    args = set(sys.argv[1:])
    if "--dry" in args:
        cfg = load_config()
        engine = build_engine(provider=lambda *a, **k: [])  # no fetch
        print(f"portfolio.yaml: {PORTFOLIO_PATH}")
        print(f"starting_balance_usd={cfg.get('starting_balance_usd')}  "
              f"candles_limit={cfg.get('candles_limit')}")
        for sc in engine._strategies:  # noqa: SLF001 - dry-run introspection only
            print(f"  {sc.id:<18} engine={sc.engine:<10} {sc.symbol:<10} {sc.timeframe:<4} risk={sc.risk_pct}")
        print(f"built {len(engine._strategies)} engines OK")  # noqa: SLF001
        return 0

    engine = build_engine()
    if "--loop" in args:
        while True:
            try:
                summary = run_guarded_cycle(engine)
                if summary is not None:
                    _print_summary(summary)
            except Exception as exc:  # noqa: BLE001 - keep the loop alive across transient fetch errors
                print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] cycle error: {exc}", file=sys.stderr)
            time.sleep(float(os.environ.get("ORUM_PAPER_INTERVAL_SECONDS", "900")))
    else:  # --once (default)
        summary = run_guarded_cycle(engine)
        if summary is not None:
            _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
