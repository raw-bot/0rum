"""Daily paper-trading report for baseline_paper_v1 (READ-ONLY observation).

Reads state/ logs and prints a per-trade ledger + daily/cumulative summary +
the setup→accept/reject funnel. Touches NOTHING: no strategy, sizing, or exit
logic — pure reporting for the freeze observation phase.

Usage:
  uv run python scripts/daily_paper_report.py [YYYY-MM-DD]   # default: today UTC
"""
from __future__ import annotations

import collections
import json
import os
import sys
from datetime import datetime, timezone

STATE = os.path.join(os.path.dirname(__file__), "..", "state")
PLAUSIBLE_MIN_PRICE = 1000.0   # filter out selftest signals (e.g. BTC "@ 100.0")
# baseline_paper_v1 starts at the first trade OPENED after max_leverage=1.0 went
# live (worker boot 2026-06-19T09:47:38Z). Trades opened before this — incl. the
# legacy ~3.5x short — are EXCLUDED from validation metrics (rule 1).
BASELINE_START = "2026-06-19T09:47:38+00:00"


def _in_baseline(t):
    return (t.get("opened_at") or "") >= BASELINE_START


def _read_jsonl(name):
    path = os.path.join(STATE, name)
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _read_json(name):
    path = os.path.join(STATE, name)
    try:
        return json.load(open(path))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _day(ts):
    return (ts or "")[:10]


def _f(v, fmt, dash="—"):
    return fmt.format(v) if isinstance(v, (int, float)) else dash


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trades = _read_jsonl("trades.jsonl")
    events = _read_jsonl("events.jsonl")
    shadow = _read_jsonl("ak_macd_local_shadow.jsonl")
    hb = _read_json("heartbeat.json")
    worker_pid = (open(os.path.join(STATE, "worker.pid")).read().strip()
                  if os.path.exists(os.path.join(STATE, "worker.pid")) else "?")

    # ---- worker health ----
    hb_ts = hb.get("ts")
    age = None
    if hb_ts:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(hb_ts)).total_seconds()
    print("=" * 100)
    print(f"DAILY PAPER REPORT — baseline_paper_v1 — {target} (UTC)")
    print("=" * 100)
    health = "OK" if (age is not None and age < 180) else "STALE/ DOWN"
    print(f"  worker pid {worker_pid} | heartbeat age {('%.0fs' % age) if age is not None else '?'} "
          f"[{health}] | action={hb.get('decision_action')} | last_price={hb.get('last_price')}")

    # ---- setup funnel (shadow log, this day) ----
    sh_day = [s for s in shadow if _day(s.get("ts")) == target]
    actions = collections.Counter(s.get("action") for s in sh_day)
    print("\n  SETUP FUNNEL (producer shadow log)")
    if actions:
        for a, c in actions.most_common():
            print(f"    {a:<22} {c}")
    else:
        print("    (no shadow entries this day)")

    # ---- accept / reject (orchestrator events, this day, realistic prices) ----
    ev_day = [e for e in events if _day(e.get("ts")) == target]
    execed = [e for e in ev_day if e.get("kind") == "external_signal_executed"]
    rejected = [e for e in ev_day if e.get("kind", "").startswith("external_signal_rejected")]
    dups = [e for e in ev_day if e.get("kind") == "external_signal_duplicate"]
    selftest = [e for e in execed if " @ " in e.get("detail", "")
                and _price_from_detail(e["detail"]) is not None
                and _price_from_detail(e["detail"]) < PLAUSIBLE_MIN_PRICE]
    print("\n  ACCEPT / REJECT (orchestrator)")
    print(f"    executed (opened)      {len(execed)}" + (f"  [{len(selftest)} look like selftest @<{PLAUSIBLE_MIN_PRICE:.0f}]" if selftest else ""))
    print(f"    rejected               {len(rejected)}")
    rr = collections.Counter((e.get("check"), _short(e.get("detail"))) for e in rejected)
    for (chk, det), c in rr.most_common():
        print(f"        - [{chk}] {det}: {c}")
    print(f"    duplicates (ignored)   {len(dups)}")

    # ---- closed-trade ledger (this day) ----
    tr_day = [t for t in trades if _day(t.get("ts")) == target]
    excluded_day = [t for t in tr_day if not _in_baseline(t)]
    print(f"\n  CLOSED TRADES — {len(tr_day)} today"
          + (f"  ({len(excluded_day)} pre-baseline, excluded from metrics)" if excluded_day else ""))
    if tr_day:
        hdr = ("    {:<5} {:<8} {:<5} {:>4} {:>9} {:>9} {:>8} {:>7} {:>6} {:>11} {:>6} {:>9} {:>7} {:>8} {:>6} {:>4}").format(
            "base", "time", "dir", "exit", "entry", "exit_px", "stopΔ", "rr_eff", "lev_req", "notion_req", "capped",
            "notion", "fees", "pnl$", "R", "held")
        print(hdr)
        for t in tr_day:
            base_mark = "  Y  " if _in_baseline(t) else " EXCL"
            stop_d = t.get("risk_distance")
            if stop_d is None and t.get("stop_loss_price") and t.get("entry_price"):
                stop_d = abs(float(t["stop_loss_price"]) - float(t["entry_price"]))
            r_mult = t.get("r_multiple")
            if r_mult is None and t.get("risk_usd"):
                r_mult = float(t.get("net_pnl_usd", 0.0)) / float(t["risk_usd"])
            print(("    {:<5} {:<8} {:<5} {:>4} {:>9} {:>9} {:>8} {:>7} {:>6} {:>11} {:>6} {:>9} {:>7} {:>8} {:>6} {:>4}").format(
                base_mark,
                (t.get("ts") or "")[11:19],
                t.get("direction", "?"),
                {"stop_loss": "SL", "take_profit": "TP"}.get(t.get("exit_reason"), (t.get("exit_reason") or "?")[:4]),
                _f(t.get("entry_price"), "{:.1f}"),
                _f(t.get("exit_price"), "{:.1f}"),
                _f(stop_d, "{:.1f}"),
                _f(t.get("effective_reward_risk"), "{:.2f}"),
                _f(t.get("leverage_requested"), "{:.1f}"),
                _f(t.get("notional_requested_usd"), "{:.0f}"),
                ("Y" if t.get("capped") else ("n" if t.get("capped") is False else "—")),
                _f(t.get("notional_usd"), "{:.0f}"),
                _f(t.get("fees_usd"), "{:.1f}"),
                _f(t.get("net_pnl_usd"), "{:+.1f}"),
                _f(r_mult, "{:+.2f}"),
                _f(t.get("held_candles"), "{:d}"),
            ))
    else:
        print("    (no trades closed today)")

    # ---- open position ----
    op = _read_json("open_position.json")
    if op:
        print(f"\n  OPEN POSITION: {op.get('direction')} entry {op.get('entry_price')} "
              f"notional ${float(op.get('notional_usd',0)):,.0f} exit_mode={op.get('exit_mode')} "
              f"SL={op.get('stop_loss_price')} TP={op.get('take_profit_price')}")

    # ---- daily + cumulative summary (baseline_paper_v1 ONLY) ----
    _summary("DAILY SUMMARY (baseline only)", [t for t in tr_day if _in_baseline(t)])
    _summary("CUMULATIVE (baseline_paper_v1)", [t for t in trades if _in_baseline(t)])
    n_excl = sum(1 for t in trades if not _in_baseline(t))
    print(f"\n  (excluded from baseline: {n_excl} pre-cap trades opened before {BASELINE_START})")
    print("=" * 100)


def _price_from_detail(detail):
    try:
        return float(detail.rsplit("@", 1)[1].strip())
    except (ValueError, IndexError):
        return None


def _short(detail, n=48):
    d = (detail or "").split(":", 1)[-1].strip()
    return d[:n]


def _summary(title, ts_list):
    print(f"\n  {title}")
    if not ts_list:
        print("    (none)")
        return
    wins = [t for t in ts_list if float(t.get("net_pnl_usd", 0)) > 0]
    losses = [t for t in ts_list if float(t.get("net_pnl_usd", 0)) <= 0]
    net = sum(float(t.get("net_pnl_usd", 0)) for t in ts_list)
    fees = sum(float(t.get("fees_usd", 0)) for t in ts_list)
    gw = sum(float(t.get("net_pnl_usd", 0)) for t in wins)
    gl = -sum(float(t.get("net_pnl_usd", 0)) for t in losses)
    pf = (gw / gl) if gl > 0 else float("inf")
    rs = []
    for t in ts_list:
        r = t.get("r_multiple")
        if r is None and t.get("risk_usd"):
            r = float(t.get("net_pnl_usd", 0)) / float(t["risk_usd"])
        if isinstance(r, (int, float)):
            rs.append(r)
    print(f"    trades {len(ts_list)} | win {len(wins)}/{len(ts_list)} ({len(wins)/len(ts_list):.0%}) "
          f"| net ${net:+,.2f} | fees ${fees:,.2f} | PF {pf:.2f}"
          + (f" | ΣR {sum(rs):+.2f} (exp {sum(rs)/len(rs):+.3f}R)" if rs else ""))


if __name__ == "__main__":
    main()
