"""Entry diagnostics: WHY do the AK MACD entries lose? Reuses the exact entry +
fixed-2R sim from scripts/backtest_runner_exit.py, then buckets each entry by
regime to find a filter that turns the net positive (or less negative)."""
import sys
sys.path.insert(0, "scripts")
import backtest_runner_exit as bt  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 15000
bars = bt.fetch_klines(n=N)
h = [b["high"] for b in bars]; l = [b["low"] for b in bars]
c = [b["close"] for b in bars]; v = [b["volume"] for b in bars]; t = [b["time"] for b in bars]
n = len(c)
from datetime import datetime, timezone
print(f"{n} bars: {datetime.fromtimestamp(t[0],timezone.utc):%Y-%m-%d} -> {datetime.fromtimestamp(t[-1],timezone.utc):%Y-%m-%d}\n")

macd = [a - b for a, b in zip(bt.ema(c, bt.MACD_FAST), bt.ema(c, bt.MACD_SLOW))]
base = bt.ema(c, bt.BASE_LEN)
ema200 = bt.ema(c, 200)
atr = bt.rma(bt.true_range(h, l, c), bt.ATR_LEN)
vma = bt.sma(v, bt.VOL_MA_LEN)
isBlue = [c[i] > base[i] + atr[i]*bt.ATR_MULT for i in range(n)]
isRed = [c[i] < base[i] - atr[i]*bt.ATR_MULT for i in range(n)]
isGray = [not isBlue[i] and not isRed[i] for i in range(n)]
grayOrRed = [isGray[i] or isRed[i] for i in range(n)]
grayOrBlue = [isGray[i] or isBlue[i] for i in range(n)]
lowN = [min(l[max(0,i-bt.SWING_LOOK+1):i+1]) for i in range(n)]
highN = [max(h[max(0,i-bt.SWING_LOOK+1):i+1]) for i in range(n)]

entries = []
for i in range(200, n-1):
    flipUp = macd[i] > macd[i-1] and macd[i-1] <= macd[i-2]
    flipDown = macd[i] < macd[i-1] and macd[i-1] >= macd[i-2]
    volOk = v[i] > vma[i]
    buy = (bt.barssince(isBlue,i) < bt.TREND_LOOK and bt.barssince(grayOrRed,i) < bt.PULL_LOOK
           and c[i] > base[i] and flipUp and macd[i] > 0 and volOk)
    sell = (bt.barssince(isRed,i) < bt.TREND_LOOK and bt.barssince(grayOrBlue,i) < bt.PULL_LOOK
            and c[i] < base[i] and flipDown and macd[i] < 0 and volOk)
    if not (buy or sell):
        continue
    d = "long" if buy else "short"
    try:
        br = bt.compute_bracket(entry_price=c[i], baseline_at_entry=base[i],
                                recent_low=lowN[i], recent_high=highN[i], direction=d, rr=bt.RR)
    except ValueError:
        continue
    entry = c[i]*(1+bt.SLIP_PCT) if d=="long" else c[i]*(1-bt.SLIP_PCT)
    r = bt.sim_fixed(d, entry, br.stop_loss_price, br.take_profit_price, br.risk_distance, h, l, i+1)
    if r is None:
        continue
    above200 = c[i] > ema200[i]
    slope_up = base[i] > base[i-20]
    with_htf = (d=="long" and above200) or (d=="short" and not above200)
    with_slope = (d=="long" and slope_up) or (d=="short" and not slope_up)
    entries.append({"d": d, "r": r, "above200": above200, "with_htf": with_htf, "with_slope": with_slope})

def rep(name, rs):
    if not rs:
        print(f"  {name:<34} 0 trades"); return
    wins = [x for x in rs if x > 0]; losses = [x for x in rs if x <= 0]
    pf = sum(wins)/abs(sum(losses)) if losses and sum(losses)!=0 else float('inf')
    print(f"  {name:<34} {len(rs):>4} tr | win {len(wins)/len(rs)*100:4.1f}% | net {sum(rs):+7.1f}R | PF {pf:.2f}")

allr = [e["r"] for e in entries]
print(f"=== ALL ENTRIES ({len(entries)}) ===")
rep("all", allr)
rep("long", [e["r"] for e in entries if e["d"]=="long"])
rep("short", [e["r"] for e in entries if e["d"]=="short"])

print("\n=== QUADRANT  direction x HTF trend (price vs EMA200) ===")
for d in ("long","short"):
    for tr in (True, False):
        lbl = f"{d} {'in UPtrend' if tr else 'in DOWNtrend'} (>EMA200={tr})"
        rep(lbl, [e["r"] for e in entries if e["d"]==d and e["above200"]==tr])

print("\n=== FILTER A: only WITH the HTF trend (long>EMA200, short<EMA200) ===")
rep("with-HTF-trend", [e["r"] for e in entries if e["with_htf"]])
rep("against-HTF-trend (dropped)", [e["r"] for e in entries if not e["with_htf"]])

print("\n=== FILTER B: only WITH baseline slope (long base rising, short falling) ===")
rep("with-slope", [e["r"] for e in entries if e["with_slope"]])
rep("against-slope (dropped)", [e["r"] for e in entries if not e["with_slope"]])

print("\n=== FILTER A+B combined ===")
rep("with-HTF AND with-slope", [e["r"] for e in entries if e["with_htf"] and e["with_slope"]])
