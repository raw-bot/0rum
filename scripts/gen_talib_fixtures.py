"""Generate TA-Lib reference fixtures for hermes_trading.dsl.indicators tests.

Run once in a venv with ta-lib installed; output is frozen in
tests/fixtures/talib_reference.json. TA-Lib is NOT a runtime dependency.
"""
import json
import numpy as np
import talib

rng = np.random.default_rng(42)
n = 120
# Deterministic synthetic OHLCV: geometric-ish walk around 65000.
closes = 65000.0 + np.cumsum(rng.normal(0, 80, n))
opens = np.concatenate([[closes[0]], closes[:-1]])
spread = np.abs(rng.normal(0, 60, n)) + 20
highs = np.maximum(opens, closes) + spread
lows = np.minimum(opens, closes) - spread
volumes = np.abs(rng.normal(10, 3, n))

def ser(a):
    return [None if np.isnan(x) else round(float(x), 10) for x in a]

upper14, middle14, lower14 = talib.BBANDS(closes, timeperiod=20, nbdevup=2.0, nbdevdn=2.0, matype=0)
upper5, middle5, lower5 = talib.BBANDS(closes, timeperiod=5, nbdevup=1.0, nbdevdn=1.0, matype=0)

fixture = {
    "candles": [
        {
            "ts": 1700000000000 + i * 60000,
            "open": round(float(opens[i]), 10),
            "high": round(float(highs[i]), 10),
            "low": round(float(lows[i]), 10),
            "close": round(float(closes[i]), 10),
            "volume": round(float(volumes[i]), 10),
        }
        for i in range(n)
    ],
    "talib_version": talib.__version__,
    "expected": {
        "rsi_14": ser(talib.RSI(closes, timeperiod=14)),
        "rsi_2": ser(talib.RSI(closes, timeperiod=2)),
        "sma_20": ser(talib.SMA(closes, timeperiod=20)),
        "sma_2": ser(talib.SMA(closes, timeperiod=2)),
        "ema_20": ser(talib.EMA(closes, timeperiod=20)),
        "ema_9": ser(talib.EMA(closes, timeperiod=9)),
        "atr_14": ser(talib.ATR(highs, lows, closes, timeperiod=14)),
        "atr_2": ser(talib.ATR(highs, lows, closes, timeperiod=2)),
        "bollinger_20_2_upper": ser(upper14),
        "bollinger_20_2_middle": ser(middle14),
        "bollinger_20_2_lower": ser(lower14),
        "bollinger_5_1_upper": ser(upper5),
        "bollinger_5_1_middle": ser(middle5),
        "bollinger_5_1_lower": ser(lower5),
    },
}
with open("tests/fixtures/talib_reference.json", "w") as fh:
    json.dump(fixture, fh, indent=1)
print("wrote", len(fixture["candles"]), "candles")
