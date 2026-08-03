"""Realistic-fill repricing of a runtime replay's ledger — portfolio level.

PaperEngine fills at the signal candle's CLOSE (a price from the past).
This module replays the SAME decision stream (the trades a runtime run
actually took, in order) against the 15m monitor bars with realistic
execution:

  entry   first 15m bar OPEN at/after the original cycle fill time,
          worsened by `slippage_bps`; refused if that open already breaches
          the frozen stop (gap-through);
  exits   protective SL/TP on the frozen strategy levels, SL first, with
          gap-through fills at the bar open and slippage on stops;
          signal exits (utbot & co) at the first 15m open after the
          original exit cycle, with slippage;
  sizing  UNCHANGED legacy basis (risk_pct x equity / atr_risk, leverage
          cap) so the experiment isolates the fill effect — equity is
          re-simulated, so position sizes compound on the repriced path.

Honest scope limit: the decision stream is frozen — cap accept/refuse
decisions are NOT re-evaluated on the repriced equity path. Divergences the
repricer cannot honour (entry skipped by gap-through, position still open
when the next entry arrives) are counted and reported, not hidden.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from scripts.replay_harness.timeline import INTERVAL_MS, SnapshotProvider

FEE_RT = 0.001


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def load_trades(ledger_dir: Path) -> list[dict]:
    """Chronological open/close pairs per strategy from the fills ledger."""
    fills = [json.loads(l) for l in (ledger_dir / "fills.jsonl").read_text().splitlines() if l.strip()]
    open_by_position: dict[str, dict] = {}
    trades: list[dict] = []
    for f in fills:
        sid = f["strategy_id"]
        position_id = f.get("position_id", sid)
        if f["action"] == "open":
            open_by_position[position_id] = f
        elif f["action"] == "close" and position_id in open_by_position:
            o = open_by_position.pop(position_id)
            trades.append({
                "sid": sid,
                "position_id": position_id,
                "side": o.get("side", "long"),
                "open_ts": _ts(o["ts"]),
                "legacy_entry": o["price"],
                "stop": o.get("stop_loss_price"),
                "tp": o.get("take_profit_price"),
                "atr_risk": o["atr_risk"],
                "risk_distance": o.get("risk_distance", o["atr_risk"]),
                "risk_pct": o["risk_pct"],
                "close_ts": _ts(f["ts"]),
                "close_reason": f["reason"],
                "legacy_exit": f["price"],
            })
    # A still-open final position is out of scope (no realized comparison).
    return sorted(trades, key=lambda t: t["open_ts"])


def reprice(trades: list[dict], provider: SnapshotProvider, *, symbol: str,
            monitor_timeframe: str = "15m", slippage_bps: float = 2.0,
            max_leverage: float | None = 3.0,
            starting_balance: float = 10_000.0) -> dict:
    bars = provider.series[(symbol, monitor_timeframe)]
    step = INTERVAL_MS[monitor_timeframe]
    slip = slippage_bps / 10_000.0

    balance = starting_balance
    open_pos: dict[str, dict] = {}
    pending = list(trades)
    next_trade = 0
    done: list[dict] = []
    skipped = {"gap_through_entry": 0, "position_still_open": 0, "no_bar": 0}
    curve: list[tuple[int, float]] = []
    peak, max_dd = 0.0, 0.0

    def close_position(sid: str, price: float, bar_ts: int, reason: str) -> None:
        nonlocal balance
        pos = open_pos.pop(sid)
        direction = 1.0 if pos["trade"].get("side", "long") == "long" else -1.0
        gross = direction * pos["qty"] * (price - pos["entry"])
        fee = pos["qty"] * price * FEE_RT / 2
        balance += gross - fee
        done.append({**pos["trade"], "real_entry": pos["entry"], "real_exit": price,
                     "real_exit_ts": bar_ts, "real_reason": reason,
                     "real_net": gross - fee - pos["entry_fee"]})

    for bar in bars:
        bar_close_ts = bar["ts"] + step
        opn, hi, lo = float(bar["open"]), float(bar["high"]), float(bar["low"])

        # -- exits first (protective SL-first, then due signal exits) --------
        for sid in list(open_pos):
            pos = open_pos[sid]
            if bar["ts"] < pos["from_ts"]:
                continue
            stop, tp = pos["trade"]["stop"], pos["trade"]["tp"]
            side = pos["trade"].get("side", "long")
            if side == "long":
                if stop is not None and lo <= stop:
                    price = (opn if opn <= stop else stop) * (1 - slip)
                    close_position(sid, price, bar["ts"], "stop_loss")
                    continue
                if tp is not None and hi >= tp:
                    close_position(sid, opn if opn >= tp else tp, bar["ts"], "take_profit")
                    continue
            else:
                if stop is not None and hi >= stop:
                    price = (opn if opn >= stop else stop) * (1 + slip)
                    close_position(sid, price, bar["ts"], "stop_loss")
                    continue
                if tp is not None and lo <= tp:
                    close_position(sid, opn if opn <= tp else tp, bar["ts"], "take_profit")
                    continue
            if (pos["trade"]["close_reason"] not in ("stop_loss", "take_profit")
                    and bar["ts"] >= pos["signal_exit_ts"]):
                exit_slip = 1 - slip if side == "long" else 1 + slip
                close_position(sid, opn * exit_slip, bar["ts"], pos["trade"]["close_reason"])

        # -- entries due at this bar -----------------------------------------
        while next_trade < len(pending) and pending[next_trade]["open_ts"] <= bar["ts"]:
            trade = pending[next_trade]
            next_trade += 1
            position_id = trade["position_id"]
            if position_id in open_pos:
                skipped["position_still_open"] += 1
                continue
            side = trade.get("side", "long")
            if side not in ("long", "short"):
                raise ValueError(f"unknown replay side {side!r}")
            entry = opn * (1 + slip if side == "long" else 1 - slip)
            gap_through = trade["stop"] is not None and (
                (side == "long" and entry <= trade["stop"])
                or (side == "short" and entry >= trade["stop"])
            )
            if gap_through:
                skipped["gap_through_entry"] += 1
                continue
            equity = balance + sum(
                (1.0 if p["trade"].get("side", "long") == "long" else -1.0)
                * p["qty"] * (float(bar["close"]) - p["entry"])
                for p in open_pos.values()
            )
            qty = trade["risk_pct"] * equity / trade["atr_risk"]
            if max_leverage and qty * entry > max_leverage * equity:
                qty = max_leverage * equity / entry
            entry_fee = qty * entry * FEE_RT / 2
            balance -= entry_fee
            open_pos[position_id] = {
                "trade": trade, "entry": entry, "qty": qty,
                "entry_fee": entry_fee, "from_ts": bar["ts"] + 1,
                "signal_exit_ts": trade["close_ts"],
            }

        equity = balance + sum(
            (1.0 if p["trade"].get("side", "long") == "long" else -1.0)
            * p["qty"] * (float(bar["close"]) - p["entry"])
            for p in open_pos.values()
        )
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
        curve.append((bar_close_ts, equity))

    # Force-close whatever is left at the last close (marked, not slipped).
    for sid in list(open_pos):
        close_position(sid, float(bars[-1]["close"]), bars[-1]["ts"], "end_of_data")

    by_sid: dict[str, float] = {}
    for t in done:
        by_sid[t["sid"]] = by_sid.get(t["sid"], 0.0) + t["real_net"]
    final = curve[-1][1] if curve else starting_balance
    return {
        "slippage_bps": slippage_bps,
        "final_equity": round(balance, 2),
        "net_return_pct": round(100 * (balance - starting_balance) / starting_balance, 2),
        "max_dd_pct_of_peak": round(100 * max_dd, 1),
        "net_by_strategy": {sid: round(v, 2) for sid, v in sorted(by_sid.items())},
        "n_trades": len(done),
        "skipped": skipped,
        "exit_reason_changes": sum(1 for t in done if t["real_reason"] != t["close_reason"]),
    }
