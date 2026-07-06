"""Validation finale avant câblage bot (PLAN_BACKTESTS.md, 2026-07-05).

Part A — COT window (or) : sensibilité des seuils + drawdown INTRA-fenêtre.
  Le résultat x2.01 @ 15% expo utilisait LO=20 / lookback=156 fixés a priori.
  Ici : grille LO x lookback, équité échantillonnée CHAQUE JOUR (le maxDD
  précédent n'était mesuré qu'aux clôtures de fenêtres -> sous-estimé).
  Robuste = toute la grille raisonnablement positive, pas un pic isolé.

Part B — Donchian 20/10 daily BTC : kill-criteria pour le live.
  Distribution des trades (R multiples vs risque d'entrée ATR14x2), pire série
  de pertes, maxDD quotidien, pire trade. Ces chiffres deviennent les seuils
  écrits AVANT lancement : live > pire backtest + marge => coupe.

Usage: uv run python scripts/validation_finale.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import fetch_cot, fetch_daily, fetch_klines, cot_index

FEE_RT = 0.001


# ---------------------------------------------------------------- Part A
def gate_series(bars, cot, lookback, lo):
    ic = cot_index([r["comm_net"] for r in cot], lookback)
    dates = [datetime.fromtimestamp(b["time"], tz=timezone.utc).strftime("%Y-%m-%d")
             for b in bars]
    g, k, cur = [False] * len(bars), 0, False
    for i, d in enumerate(dates):
        while k < len(cot) and cot[k]["usable_from"] <= d:
            if k >= lookback:
                cur = ic[k] <= lo
            k += 1
        g[i] = cur
    return g


def hold_daily_equity(bars, gate):
    """Long pendant la fenêtre, équité quotidienne (DD intra-fenêtre capturé)."""
    o = [b["open"] for b in bars]; c = [b["close"] for b in bars]
    eq, pos, px, tr, wins = [1.0], 0, 0.0, 0, 0
    base = 1.0
    for i in range(1, len(bars)):
        if pos == 0 and gate[i - 1]:
            pos, px, tr = 1, o[i] * (1 + FEE_RT / 2), tr + 1
        elif pos == 1 and not gate[i - 1]:
            x = o[i] * (1 - FEE_RT / 2)
            wins += x > px
            base *= x / px
            pos = 0
        eq.append(base * (c[i] / px if pos else 1.0))
    if pos:
        x = c[-1] * (1 - FEE_RT / 2)
        wins += x > px
        base *= x / px
        eq[-1] = base
    peak, mdd = 1.0, 0.0
    for e in eq:
        peak = max(peak, e)
        mdd = max(mdd, 1 - e / peak)
    return base, mdd, tr, wins, sum(gate) / len(gate)


def part_a():
    bars = fetch_daily("GC=F", "2005-01-01")
    cot = fetch_cot(range(2006, 2027), "GOLD - COMMODITY EXCHANGE")
    print("=" * 96)
    print("A. COT WINDOW OR — grille seuils, équité QUOTIDIENNE (DD intra-fenêtre réel)")
    print("=" * 96)
    print(f"  {'':10s}" + "".join(f"  lookback {lb:3d}w                " for lb in (104, 156, 208)))
    for lo in (15.0, 20.0, 25.0, 30.0):
        line = f"  comm<={lo:2.0f}  "
        for lb in (104, 156, 208):
            g = gate_series(bars, cot, lb, lo)
            total, mdd, tr, wins, expo = hold_daily_equity(bars, g)
            line += f"| x{total:5.2f} DD{mdd*100:5.1f}% {tr:2d}tr {100*wins/max(1,tr):3.0f}%w e{expo*100:4.1f}% "
        print(line)


# ---------------------------------------------------------------- Part B
def part_b():
    bars = fetch_klines("BTCUSDT", "1d", 3200)
    o = [b["open"] for b in bars]; h = [b["high"] for b in bars]
    l = [b["low"] for b in bars]; c = [b["close"] for b in bars]
    n = len(bars)
    # ATR14 pour exprimer chaque trade en R (risque = 2*ATR à l'entrée)
    tr_ = [h[0] - l[0]]
    for i in range(1, n):
        tr_.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    a, atr = 1 / 14, [tr_[0]]
    for x in tr_[1:]:
        atr.append(a * x + (1 - a) * atr[-1])

    eq, pos, px, risk = [1.0], 0, 0.0, 0.0
    rs, base, daily = [], 1.0, [1.0]
    for i in range(22, n):
        hi = max(c[i - 21:i - 1]); lo10 = min(c[i - 11:i - 1])
        if pos == 0 and c[i - 1] > hi:
            pos, px, risk = 1, o[i] * (1 + FEE_RT / 2), 2 * atr[i - 1]
        elif pos == 1 and c[i - 1] < lo10:
            x = o[i] * (1 - FEE_RT / 2)
            rs.append((x - px) / risk)
            base *= x / px
            pos = 0
        daily.append(base * (c[i] / px if pos else 1.0))
    if pos:
        x = c[-1] * (1 - FEE_RT / 2)
        rs.append((x - px) / risk)
        base *= x / px
        daily[-1] = base

    peak, mdd = 1.0, 0.0
    for e in daily:
        peak = max(peak, e)
        mdd = max(mdd, 1 - e / peak)
    streak, worst_streak = 0, 0
    for r in rs:
        streak = streak + 1 if r < 0 else 0
        worst_streak = max(worst_streak, streak)
    wins = [r for r in rs if r > 0]; losses = [r for r in rs if r <= 0]
    print("\n" + "=" * 96)
    print("B. DONCHIAN 20/10 DAILY BTC — kill-criteria (distribution backtest 2017-2026)")
    print("=" * 96)
    print(f"  trades {len(rs)} | win {100*len(wins)/len(rs):.1f}% | avg win {sum(wins)/len(wins):+.2f}R "
          f"| avg loss {sum(losses)/len(losses):+.2f}R | pire trade {min(rs):+.2f}R")
    print(f"  équité x{base:.2f} | maxDD quotidien {mdd*100:.1f}% | pire série de pertes {worst_streak}")
    print(f"  KILL-CRITERIA proposés (à écrire dans goal.yaml avant lancement) :")
    print(f"    - série de pertes live > {worst_streak + 3}  (pire backtest {worst_streak} + marge 3)")
    print(f"    - drawdown live > {mdd*100*1.25:.0f}%  (pire backtest {mdd*100:.0f}% x 1.25)")
    print(f"    - un trade pire que {min(rs)-1:.1f}R  (jamais vu en backtest => bug harnais/exécution)")


if __name__ == "__main__":
    part_a()
    part_b()
