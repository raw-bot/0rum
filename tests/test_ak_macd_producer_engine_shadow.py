"""Tests for AkMacdProducer._check_engine_parity: logs (never raises, never
affects routing) when AkMacdEngine disagrees with the evaluate_ak_macd_verdict
result poll_once() already computed -- the function that actually routes
live (paper) trades right now (goal.yaml: signal_source=tradingview_external).
"""

import unittest
from unittest.mock import patch

from orum.external.ak_macd import AkMacdParams, evaluate_ak_macd_verdict
from orum.external.ak_macd_producer import AkMacdProducer

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

    for _ in range(150):
        o = price
        price += rng.uniform(-0.4, 0.4)
        _append(o, price, 10.0 + rng.uniform(0, 2))

    o = price
    price -= 1.2
    _append(o, price, 8.0)

    for i in range(7):
        o = price
        price += 0.9 + i * 0.5
        _append(o, price, 80.0)

    return candles


CANDLES = _make_candles()
PARAMS = AkMacdParams()


def _producer() -> AkMacdProducer:
    return AkMacdProducer(
        reader=lambda: [],
        params=PARAMS,
        goal_loader=lambda: {"allowed_external_strategies": []},
        printer=None,
    )


class EngineParityShadowTests(unittest.TestCase):
    def test_no_log_when_engine_agrees_on_confirmed_buy(self):
        candles = CANDLES[:124]
        verdict = evaluate_ak_macd_verdict(candles, PARAMS, symbol="BTCUSDT", timeframe="15m")
        self.assertEqual(verdict.event, "BUY_CANDIDATE")

        with patch("orum.external.ak_macd_producer.log_event") as mock_log:
            _producer()._check_engine_parity(candles, verdict)

        mock_log.assert_not_called()

    def test_no_log_when_engine_agrees_on_no_setup(self):
        candles = CANDLES[: PARAMS.warmup + 5]
        verdict = evaluate_ak_macd_verdict(candles, PARAMS, symbol="BTCUSDT", timeframe="15m")
        self.assertIsNone(verdict.payload)

        with patch("orum.external.ak_macd_producer.log_event") as mock_log:
            _producer()._check_engine_parity(candles, verdict)

        mock_log.assert_not_called()

    def test_logs_when_verdict_disagrees_with_the_engine(self):
        candles = CANDLES[:124]
        real_verdict = evaluate_ak_macd_verdict(candles, PARAMS, symbol="BTCUSDT", timeframe="15m")
        self.assertEqual(real_verdict.event, "BUY_CANDIDATE")
        from dataclasses import replace

        forged_verdict = replace(real_verdict, event="SELL_CANDIDATE")

        with patch("orum.external.ak_macd_producer.log_event") as mock_log:
            _producer()._check_engine_parity(candles, forged_verdict)

        mock_log.assert_called_once()
        kind = mock_log.call_args.args[0]
        self.assertEqual(kind, "strategy_engine_shadow_disagreement")
        self.assertEqual(mock_log.call_args.kwargs["shadow"], "long")
        self.assertEqual(mock_log.call_args.kwargs["legacy"], "short")

    def test_no_candles_is_a_silent_no_op(self):
        with patch("orum.external.ak_macd_producer.log_event") as mock_log:
            _producer()._check_engine_parity([], evaluate_ak_macd_verdict([], PARAMS, symbol="BTCUSDT"))

        mock_log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
