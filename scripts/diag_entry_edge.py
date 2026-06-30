"""Do the LONG/SHORT entry markers have RAW DIRECTIONAL EDGE, independent of any
stop/exit? If yes -> entries call the pivot, the bracket exit is the problem.
If no -> the visual accuracy is illusory. Measures forward return + MFE/MAE."""
import sys, statistics as st
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
isBlue=[c[i]>base[i]+atr[i]*bt.ATR_MULT for i in range(n)]; isRed=[c[i]<base[i]-atr[i]*bt.ATR_MULT for i in range(n)]
isGray=[not isBlue[i] and not isRed[i] for i in range(n)]; gOr=[isGray[i] or isRed[i] for i in range(n)]; gOb=[isGray[i] or isBlue[i] for i in range(n)]
lowN=[min(l[max(0,i-bt.SWING_LOOK+1):i+1]) for i in range(n)]; highN=[max(h[max(0,i-bt.SWING_LOOK+1):i+1]) for i in range(n)]

ent=[]
for i in range(200,n-1):
    fU=macd[i]>macd[i-1] and macd[i-1]<=macd[i-2]; fD=macd[i]<macd[i-1] and macd[i-1]>=macd[i-2]; vk=v[i]>vma[i]
    buy=bt.barssince(isBlue,i)<bt.TREND_LOOK and bt.barssince(gOr,i)<bt.PULL_LOOK and c[i]>base[i] and fU and macd[i]>0 and vk
    sell=bt.barssince(isRed,i)<bt.TREND_LOOK and bt.barssince(gOb,i)<bt.PULL_LOOK and c[i]<base[i] and fD and macd[i]<0 and vk
    if buy or sell: ent.append((i,"long" if buy else "short"))

def fwd(i,k,d):
    if i+k>=n: return None
    return (c[i+k]-c[i])/c[i] if d=="long" else (c[i]-c[i+k])/c[i]

def excursions(i,d,win=20):
    # MFE/MAE over the next `win` bars, in ATR units
    mfe=mae=0.0
    for j in range(i+1,min(i+win+1,n)):
        if d=="long":
            mfe=max(mfe,h[j]-c[i]); mae=max(mae,c[i]-l[j])
        else:
            mfe=max(mfe,c[i]-l[j]); mae=max(mae,h[j]-c[i])
    a=atr[i] or 1
    return mfe/a, mae/a

for grp,sel in (("ALL",lambda d:True),("LONG",lambda d:d=="long"),("SHORT",lambda d:d=="short")):
    es=[(i,d) for (i,d) in ent if sel(d)]
    print(f"=== {grp}  ({len(es)} entries) ===")
    print("  raw directional forward return (NO stop):")
    for k in (5,10,20,40):
        rs=[fwd(i,k,d) for (i,d) in es if fwd(i,k,d) is not None]
        pos=sum(1 for x in rs if x>0)/len(rs)*100
        print(f"    +{k:>2} bars: median {st.median(rs)*100:+.3f}%  mean {sum(rs)/len(rs)*100:+.3f}%  | {pos:.0f}% in right direction")
    mfes=[]; maes=[]
    for (i,d) in es:
        f,a=excursions(i,d); mfes.append(f); maes.append(a)
    print(f"  excursions over next 20 bars (ATR units):  MFE median {st.median(mfes):.2f}  |  MAE median {st.median(maes):.2f}  |  MFE/MAE {st.median(mfes)/max(st.median(maes),1e-9):.2f}")
    print()
