"""Parité miroir Pine vs cerveau Python : liste les entrées LONG confirmées
sur BTCUSDT 4h (mêmes règles que le live : AkMacdParams défauts, long-only)."""
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading")
sys.path.insert(0, "/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading/scripts")

from backtest_4h_validation import fetch_klines, confirmed_entries
from orum.external.ak_macd import AkMacdParams, compute_state

n = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
bars = fetch_klines(symbol="BTCUSDT", interval="4h", n=n)
# La dernière barre Binance peut être en cours de formation -> on la retire
# (le miroir Pine ne signale qu'à la clôture).
bars = bars[:-1]
params = AkMacdParams()
st = compute_state(bars, params)
ts = [b["ts"] // 1000 for b in bars]
print(f"bars={len(bars)}  {datetime.fromtimestamp(ts[0], timezone.utc):%Y-%m-%d %H:%M} -> "
      f"{datetime.fromtimestamp(ts[-1], timezone.utc):%Y-%m-%d %H:%M} UTC")
longs = [t for (t, d) in confirmed_entries(st, params) if d == "long"]
print(f"entrées LONG confirmées: {len(longs)}")
for t in longs[-15:]:
    print(f"  BUY {datetime.fromtimestamp(ts[t], timezone.utc):%Y-%m-%d %H:%M} UTC  close={st.closes[t]:.1f}")
