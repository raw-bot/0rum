"""Deterministic self-test for the AK MACD shadow bridge.

Proves the test criteria WITHOUT waiting for a live chart signal, by injecting
simulated `data tables` responses: BUY detected, EXIT detected, zero duplicates,
intrabar/non-closed rejected, wrong symbol/timeframe rejected. Shadow mode =>
no orchestrator, no trades, no state mutation. Writes only to a temp log.

Run:  uv run python scripts/ak_macd_bridge_selftest.py
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from orum.external.bridge import AkMacdBridge, STUDY_NAME

TF_MS = 900_000                       # 15m
BAR = 1_781_625_600_000               # aligned: BAR % TF_MS == 0
NOW = BAR + TF_MS + 2_000             # just after the bar closed (fresh)


def reader_for(payload: dict | str):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    resp = {"success": True, "studies": [{"name": STUDY_NAME, "tables": [{"rows": [f"payload | {raw}"]}]}]}
    return lambda: resp


def state(position=None):
    return lambda: {
        "open_position": position,
        "recent_trades": (),
        "resume_ack": False,
        "trading_mode": "paper",
        "price_offline": False,
    }


def payload(**over):
    base = {
        "source": "tradingview", "strategy": "ak_macd_15m_v1", "symbol": "BTCUSD",
        "timeframe": "15m", "event": "BUY_CANDIDATE", "bar_time": BAR, "price": 66000.0,
        "version": "tv_ak_macd_15m_v1",
    }
    base.update(over)
    return base


_tmpdir = Path(tempfile.mkdtemp())
_n = [0]


def fresh_log() -> Path:
    _n[0] += 1
    return _tmpdir / f"shadow_{_n[0]}.jsonl"


def bridge(reader, position=None, now=NOW):
    # Fresh log per scenario => isolated dedup state (no bleed between cases).
    return AkMacdBridge(reader=reader, shadow=True, state_loader=state(position),
                        log_path=fresh_log(), now_ms=now, printer=lambda _m: None)


def main() -> None:
    results: list[tuple[str, bool, str]] = []

    def check(name, cond, got):
        results.append((name, bool(cond), got))

    # 1. BUY detected -> accepted (no open position).
    v = bridge(reader_for(payload(event="BUY_CANDIDATE"))).poll_once()
    check("BUY detected & accepted", v.action == "accepted" and v.event == "BUY_CANDIDATE", v.action)

    # 2. EXIT detected -> accepted (open position present).
    v = bridge(reader_for(payload(event="EXIT")), position={"asset": "BTC/USDT"}).poll_once()
    check("EXIT detected & accepted", v.action == "accepted" and v.event == "EXIT", v.action)

    # 3. Zero duplicates: same key twice on ONE bridge -> 2nd is duplicate.
    b = bridge(reader_for(payload(event="BUY_CANDIDATE")))
    first = b.poll_once()
    second = b.poll_once()
    check("duplicate rejected on re-poll", first.action == "accepted" and second.action == "duplicate", second.action)

    # 4a. Intrabar: bar_time not aligned to the 15m grid -> rejected (bar_closed).
    v = bridge(reader_for(payload(bar_time=BAR + 1_000))).poll_once()
    check("intrabar (misaligned) rejected", v.action == "rejected" and "bar" in v.detail.lower(), f"{v.action}:{v.detail}")

    # 4b. Not-closed: now < bar_time + tf -> rejected (bar_closed).
    v = bridge(reader_for(payload()), now=BAR + 1_000).poll_once()
    check("intrabar (not closed yet) rejected", v.action == "rejected" and "closed" in v.detail.lower(), f"{v.action}:{v.detail}")

    # 5a. Wrong symbol -> rejected (symbol).
    v = bridge(reader_for(payload(symbol="ETHUSD"))).poll_once()
    check("wrong symbol rejected", v.action == "rejected" and v.detail.startswith("symbol"), f"{v.action}:{v.detail}")

    # 5b. Wrong timeframe -> rejected (timeframe). Fresh `now` for 5m so it is
    #     neither stale nor a duplicate of the 15m cases.
    v = bridge(reader_for(payload(timeframe="5m", bar_time=BAR)), now=BAR + 300_000 + 2_000).poll_once()
    check("wrong timeframe rejected", v.action == "rejected" and v.detail.startswith("timeframe"), f"{v.action}:{v.detail}")

    # 6. No signal yet (emitter idle) -> no_signal, never a trade.
    v = bridge(reader_for("none")).poll_once()
    check("idle emitter -> no_signal", v.action == "no_signal", v.action)

    # 7. Unreadable table -> no_table (graceful).
    v = bridge(lambda: {"success": False}).poll_once()
    check("unreadable table -> no_table", v.action == "no_table", v.action)

    print(f"{'CRITERION':42s} {'RESULT':6s}  detail")
    print("-" * 78)
    ok = True
    for name, passed, got in results:
        ok = ok and passed
        print(f"{name:42s} {'PASS' if passed else 'FAIL':6s}  {got}")
    print("-" * 78)
    print("ALL PASS ✅" if ok else "SOME FAILED ❌")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
