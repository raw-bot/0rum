"""Are the blue/red colored-baseline segments PRECISE (predictive), and does the
AK MACD strategy follow them? Reuses the exact indicators from the backtest."""
import sys
sys.path.insert(0, "scripts")
import backtest_runner_exit as bt  # noqa

N = int(sys.argv[1]) if len(sys.argv) > 1 else 15000
bars = bt.fetch_klines(n=N)
h=[b["high"] for b in bars]; l=[b["low"] for b in bars]; c=[b["close"] for b in bars]; v=[b["volume"] for b in bars]; t=[b["time"] for b in bars]
n=len(c)
from datetime import datetime, timezone
print(f"{n} bars {datetime.fromtimestamp(t[0],timezone.utc):%Y-%m-%d}->{datetime.fromtimestamp(t[-1],timezone.utc):%Y-%m-%d}\n")

macd=[a-b for a,b in zip(bt.ema(c,bt.MACD_FAST),bt.ema(c,bt.MACD_SLOW))]
base=bt.ema(c,bt.BASE_LEN); atr=bt.rma(bt.true_range(h,l,c),bt.ATR_LEN); vma=bt.sma(v,bt.VOL_MA_LEN)
color=[]
for i in range(n):
    if c[i]>base[i]+atr[i]*bt.ATR_MULT: color.append("blue")
    elif c[i]<base[i]-atr[i]*bt.ATR_MULT: color.append("red")
    else: color.append("gray")

# ---- 1) PREDICTIVENESS: forward return by current color ----
def fwd(i,k): return (c[i+k]-c[i])/c[i] if i+k<n else None
print("=== 1) Is the color PRECISE? forward return after each color state ===")
for col in ("blue","gray","red"):
    for k in (5,20):
        rs=[fwd(i,k) for i in range(n-k) if color[i]==col]
        rs=[x for x in rs if x is not None]
        pos=sum(1 for x in rs if x>0)/len(rs)*100
        print(f"  {col:<5} +{k:>2} bars: avg {sum(rs)/len(rs)*100:+.3f}%  ({pos:.0f}% up)  n={len(rs)}")
    print()

# ---- 2) FOLLOW THE COLOR: trend-follow, hold through gray, flip on blue<->red ----
FEE=0.0008
def follow(hold_gray):
    pos=0; entry=0.0; eq=0.0; nflip=0; wins=0; tr=0
    seg=[]
    for i in range(1,n):
        want = 1 if color[i]=="blue" else (-1 if color[i]=="red" else (pos if hold_gray else 0))
        if want!=pos:
            if pos!=0:  # close
                ret=pos*(c[i]-entry)/entry - FEE; eq+=ret; tr+=1; wins+= ret>0; seg.append(ret)
            if want!=0: entry=c[i]
            pos=want; nflip+=1
    return eq,tr,wins,nflip
for hg,lbl in ((False,"strict (flat on gray)"),(True,"hold through gray")):
    eq,tr,wins,nf=follow(hg)
    print(f"=== 2) FOLLOW color, {lbl}: total {eq*100:+.1f}%  | {tr} trades win {wins/max(tr,1)*100:.0f}% | {nf} flips ===")
bh=(c[-1]-c[0])/c[0]*100
print(f"    (buy&hold over period: {bh:+.1f}%)\n")

# ---- 3) DOES THE STRATEGY FOLLOW THE COLOR? entry color context ----
isBlue=[color[i]=="blue" for i in range(n)]; isRed=[color[i]=="red" for i in range(n)]
isGray=[color[i]=="gray" for i in range(n)]; gOr=[isGray[i] or isRed[i] for i in range(n)]; gOb=[isGray[i] or isBlue[i] for i in range(n)]
lowN=[min(l[max(0,i-bt.SWING_LOOK+1):i+1]) for i in range(n)]; highN=[max(h[max(0,i-bt.SWING_LOOK+1):i+1]) for i in range(n)]
ent=[]
for i in range(200,n-1):
    fU=macd[i]>macd[i-1] and macd[i-1]<=macd[i-2]; fD=macd[i]<macd[i-1] and macd[i-1]>=macd[i-2]; vk=v[i]>vma[i]
    buy=bt.barssince(isBlue,i)<bt.TREND_LOOK and bt.barssince(gOr,i)<bt.PULL_LOOK and c[i]>base[i] and fU and macd[i]>0 and vk
    sell=bt.barssince(isRed,i)<bt.TREND_LOOK and bt.barssince(gOb,i)<bt.PULL_LOOK and c[i]<base[i] and fD and macd[i]<0 and vk
    if not(buy or sell): continue
    d="long" if buy else "short"
    try: br=bt.compute_bracket(entry_price=c[i],baseline_at_entry=base[i],recent_low=lowN[i],recent_high=highN[i],direction=d,rr=bt.RR)
    except ValueError: continue
    e=c[i]*(1+bt.SLIP_PCT) if d=="long" else c[i]*(1-bt.SLIP_PCT)
    r=bt.sim_fixed(d,e,br.stop_loss_price,br.take_profit_price,br.risk_distance,h,l,i+1)
    if r is None: continue
    # bars since the aligned color (long wants recent blue, short recent red)
    bs=bt.barssince(isBlue,i) if d=="long" else bt.barssince(isRed,i)
    ent.append({"d":d,"r":r,"color":color[i],"bars_since_aligned":bs})

def rep(name,rs):
    if not rs: print(f"  {name:<30} 0"); return
    w=[x for x in rs if x>0]; loss=[x for x in rs if x<=0]
    pf=sum(w)/abs(sum(loss)) if loss and sum(loss) else float('inf')
    print(f"  {name:<30} {len(rs):>4} tr | win {len(w)/len(rs)*100:4.1f}% | net {sum(rs):+7.1f}R | PF {pf:.2f}")
print(f"=== 3) Do the {len(ent)} entries FOLLOW the color? ===")
print("  color state AT entry:")
from collections import Counter
for col in ("blue","gray","red"):
    rep(f"  entered while {col}", [e["r"] for e in ent if e["color"]==col])
print("  freshness of the aligned color (bars since last blue[long]/red[short]):")
rep("  fresh (<=3 bars)", [e["r"] for e in ent if e["bars_since_aligned"]<=3])
rep("  stale (>10 bars)", [e["r"] for e in ent if e["bars_since_aligned"]>10])
