"""Pins AkMacdEngine.on_candle() as equivalent to evaluate_ak_macd_verdict(),
the function ak_macd_producer.py calls directly today. The engine is not
wired into the producer yet; this is what makes a future cutover safe.

_make_candles() is one deterministic synthetic series (seeded random walk +
a scripted breakout) that happens to produce a confirmed SELL_CANDIDATE,
then two confirmed BUY_CANDIDATEs, as the buffer grows -- found by sweeping
buffer lengths against evaluate_ak_macd_verdict directly (see commit
history), not hand-tuned to "look like" a trade. Slicing prefixes of it
gives real confirmed/no_setup cases without depending on live market data.
"""

import unittest

from orum.external.ak_macd import AkMacdParams, evaluate_ak_macd_verdict
from orum.strategies.ak_macd import AkMacdEngine
from orum.strategies.base import Side, StrategyContext

START_TS = 1_781_424_000_000
BAR_MS = 900_000


def _make_candles() -> list[dict]:
    import random

    candles: list[dict] = []
    price = 100.0
    rng = random.Random(11)

    def _append(open_price: float, close_price: float, volume: float) -> None:
        candles.append({
            "ts": START_TS + len(candles) * BAR_MS,
            "open": open_price,
            "high": max(open_price, close_price) + 0.1,
            "low": min(open_price, close_price) - 0.1,
            "close": close_price,
            "volume": volume,
        })

    for _ in range(150):  # sideways chop: baseline tracks price, natural blue/red/gray weave
        o = price
        price += rng.uniform(-0.4, 0.4)
        _append(o, price, 10.0 + rng.uniform(0, 2))

    o = price  # one clear dip bar so MACD turns down then up right before the thrust (flip_up)
    price -= 1.2
    _append(o, price, 8.0)

    for i in range(7):  # accelerating green, high-volume breakout
        o = price
        price += 0.9 + i * 0.5
        _append(o, price, 80.0)

    return candles


CANDLES = _make_candles()
PARAMS = AkMacdParams()


def _context(candles: list[dict]) -> StrategyContext:
    return StrategyContext(candles=candles, symbol="BTCUSDT", timeframe="15m")


def _engine() -> AkMacdEngine:
    engine = AkMacdEngine()
    engine.init({})
    return engine


class AkMacdEngineParityTests(unittest.TestCase):
    def test_confirmed_sell_candidate_matches_verdict(self):
        candles = CANDLES[:120]
        verdict = evaluate_ak_macd_verdict(candles, PARAMS, symbol="BTCUSDT", timeframe="15m")
        self.assertEqual(verdict.event, "SELL_CANDIDATE")

        signal = _engine().on_candle(candles[-1], _context(candles))

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.SHORT)
        self.assertEqual(signal.entry_reason, verdict.reason)
        self.assertEqual(signal.strategy_metadata["payload"], verdict.payload)

    def test_confirmed_buy_candidate_matches_verdict(self):
        candles = CANDLES[:124]
        verdict = evaluate_ak_macd_verdict(candles, PARAMS, symbol="BTCUSDT", timeframe="15m")
        self.assertEqual(verdict.event, "BUY_CANDIDATE")

        signal = _engine().on_candle(candles[-1], _context(candles))

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.LONG)
        self.assertEqual(signal.entry_reason, verdict.reason)
        self.assertEqual(signal.strategy_metadata["payload"], verdict.payload)

    def test_no_setup_produces_no_signal(self):
        candles = CANDLES[: PARAMS.warmup + 5]
        verdict = evaluate_ak_macd_verdict(candles, PARAMS, symbol="BTCUSDT", timeframe="15m")
        self.assertIsNone(verdict.payload)

        self.assertIsNone(_engine().on_candle(candles[-1], _context(candles)))

    def test_insufficient_warmup_produces_no_signal(self):
        candles = CANDLES[:10]
        self.assertIsNone(_engine().on_candle(candles[-1], _context(candles)))

    def test_full_buffer_matches_verdict_for_every_growing_prefix(self):
        # Structural equivalence, not just the two hand-picked confirmations
        # above: whatever evaluate_ak_macd_verdict decides at every length,
        # the engine's mapping rule (payload+event -> Signal, else None) holds.
        for end in range(PARAMS.warmup + 1, len(CANDLES) + 1, 7):
            candles = CANDLES[:end]
            verdict = evaluate_ak_macd_verdict(candles, PARAMS, symbol="BTCUSDT", timeframe="15m")
            signal = _engine().on_candle(candles[-1], _context(candles))
            if verdict.payload is None or verdict.event not in ("BUY_CANDIDATE", "SELL_CANDIDATE"):
                self.assertIsNone(signal, f"expected no signal at end={end}, action={verdict.action}")
            else:
                self.assertIsNotNone(signal, f"expected a signal at end={end}, event={verdict.event}")
                expected_side = Side.LONG if verdict.event == "BUY_CANDIDATE" else Side.SHORT
                self.assertEqual(signal.side, expected_side)


class AkMacdEngineConfigOverrideTests(unittest.TestCase):
    def test_valid_overrides_are_applied(self):
        engine = AkMacdEngine()
        engine.init({"confirmation_bars": 5, "candidate_window_bars": 3, "regime_filter": False, "require_candle_direction": False})
        self.assertEqual(engine._params.confirmation_bars, 5)
        self.assertEqual(engine._params.candidate_window_bars, 3)
        self.assertFalse(engine._params.regime_filter)
        self.assertFalse(engine._params.require_candle_direction)

    def test_invalid_overrides_fall_back_to_defaults(self):
        defaults = AkMacdParams()
        engine = AkMacdEngine()
        engine.init({"confirmation_bars": -1, "candidate_window_bars": "two", "regime_filter": "yes"})
        self.assertEqual(engine._params.confirmation_bars, defaults.confirmation_bars)
        self.assertEqual(engine._params.candidate_window_bars, defaults.candidate_window_bars)
        self.assertEqual(engine._params.regime_filter, defaults.regime_filter)

    def test_missing_or_non_dict_config_uses_defaults(self):
        engine = AkMacdEngine()
        engine.init({})
        self.assertEqual(engine._params, AkMacdParams())
        engine.init(None)
        self.assertEqual(engine._params, AkMacdParams())


if __name__ == "__main__":
    unittest.main()
