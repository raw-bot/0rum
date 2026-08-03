"""Tests for the SSL band trail: pure helpers + the close_position_if_needed wiring.

The trail adds two exits on top of the frozen bracket (opt-in via risk.trail):
  1. one-way ratchet of the stop toward baseline -/+ k*ATR (only tightens);
  2. invalidation close when price closes back through the band (the "red line").
"""

import unittest
from unittest.mock import patch

from orum.external.bracket import band_invalidated, ssl_band, trail_stop
from orum.loop import close_position_if_needed


class TestSslBandHelpers(unittest.TestCase):
    def test_band_is_baseline_plus_minus_k_atr(self):
        lo, up = ssl_band(baseline=100.0, atr=2.0, atr_mult=1.0)
        self.assertEqual((lo, up), (98.0, 102.0))
        lo2, _ = ssl_band(baseline=100.0, atr=2.0, atr_mult=0.2)  # the dot-color buffer
        self.assertAlmostEqual(lo2, 99.6)

    def test_trail_long_only_tightens_up(self):
        # band_lo 98 above the structural stop 95 -> ratchet up to 98.
        self.assertEqual(trail_stop(direction="long", stop_loss_price=95.0, baseline=100.0, atr=2.0, atr_mult=1.0), 98.0)
        # band_lo 98 below an already-higher stop 99 -> never loosen, keep 99.
        self.assertEqual(trail_stop(direction="long", stop_loss_price=99.0, baseline=100.0, atr=2.0, atr_mult=1.0), 99.0)

    def test_trail_short_only_tightens_down(self):
        self.assertEqual(trail_stop(direction="short", stop_loss_price=105.0, baseline=100.0, atr=2.0, atr_mult=1.0), 102.0)
        self.assertEqual(trail_stop(direction="short", stop_loss_price=101.0, baseline=100.0, atr=2.0, atr_mult=1.0), 101.0)

    def test_invalidation_long_below_band_short_above_band(self):
        self.assertTrue(band_invalidated(direction="long", close=97.9, baseline=100.0, atr=2.0, atr_mult=1.0))
        self.assertFalse(band_invalidated(direction="long", close=98.1, baseline=100.0, atr=2.0, atr_mult=1.0))
        self.assertTrue(band_invalidated(direction="short", close=102.1, baseline=100.0, atr=2.0, atr_mult=1.0))
        self.assertFalse(band_invalidated(direction="short", close=101.9, baseline=100.0, atr=2.0, atr_mult=1.0))


def _feed(last_close=100.0):
    """A 15m CLOSED-bar series (what price.recent_closed_candles returns): 39 flat
    bars (EMA30=100, ATR14=2 -> band_lo=98) plus a last closed bar at `last_close`."""
    bars = [{"ts": i, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0} for i in range(39)]
    bars.append({"ts": 39, "open": 100.0, "high": max(101.0, last_close),
                 "low": min(99.0, last_close), "close": last_close, "volume": 1.0})
    return bars


def _close(position, strategy, market, *, feed=None):
    """close_position_if_needed with the 15m feed stubbed (closed bars)."""
    with patch("orum.adapters.price.recent_closed_candles", return_value=feed if feed is not None else _feed()):
        return close_position_if_needed(position, strategy, market, rsi=None)
# Default: invalidation on, ratchet OFF (the shipped config after the 1m-whipsaw fix).
_TRAIL_STRATEGY = {
    "risk": {
        "stop_loss_pct": 0.001, "take_profit_pct": 0.001, "max_hold_candles": 1, "fee_rate": 0.0,
        "trail": {"enabled": True, "atr_mult": 1.0, "base_len": 30, "atr_len": 14,
                  "invalidate": True, "ratchet": False},
    }
}
# Opt-in ratchet variant (only used to prove the ratchet still works when enabled).
_RATCHET_STRATEGY = {
    "risk": {
        "stop_loss_pct": 0.001, "take_profit_pct": 0.001, "max_hold_candles": 1, "fee_rate": 0.0,
        "trail": {"enabled": True, "atr_mult": 1.0, "base_len": 30, "atr_len": 14,
                  "invalidate": True, "ratchet": True},
    }
}


def _bracket_position(direction="long", stop=95.0):
    return {
        "exit_mode": "bracket", "direction": direction, "entry_price": 100.0,
        "stop_loss_price": stop, "take_profit_price": 110.0 if direction == "long" else 90.0,
        "notional_usd": 1000.0, "qty_base": 10.0, "risk_usd": 50.0,
        "opened_candle_ts": 0, "candle_interval_ms": 900_000,
    }


def _market(last_close):
    # Only closes[-1] matters now: the trail reads its own 15m feed, the bracket
    # check uses the current close. The 1m candles here must NOT feed the band.
    return {"closes": [100.0] * 39 + [last_close], "last_candle_ts": 10**12, "source": "tradingview"}


class TestTrailInCloseLoop(unittest.TestCase):
    def test_ratchet_off_survives_1m_wick_when_15m_inside_band(self):
        # Regression for the 2026-06-29 whipsaw: a 1m price dips below the band
        # (95 < band_lo 98) but the closed 15m bar is still inside (100). With the
        # ratchet OFF (default), the frozen stop (90) is NOT tightened, so the 1m
        # wick does not stop the trade out — it survives, as it should have.
        pos = _bracket_position("long", stop=90.0)
        out = _close(pos, _TRAIL_STRATEGY, _market(95.0), feed=_feed(100.0))
        self.assertIsNone(out)                       # no exit: 1m wick can't whipsaw a frozen stop
        self.assertEqual(pos["stop_loss_price"], 90.0)  # frozen stop untouched (no ratchet)

    def test_ratchet_when_enabled_and_in_profit_tightens(self):
        # Price 101 > entry 100 -> in profit -> the ratchet engages.
        pos = _bracket_position("long", stop=95.0)
        out = _close(pos, _RATCHET_STRATEGY, _market(101.0), feed=_feed(100.0))
        self.assertIsNone(out)                       # closed 15m bar inside band -> stays open
        self.assertEqual(pos["stop_loss_price"], 98.0)  # ratcheted 95 -> band_lo 98
        self.assertEqual(pos["sl_basis"], "ssl_trail")

    def test_ratchet_does_NOT_tighten_when_underwater(self):
        # Price 99 < entry 100 -> the long is underwater. Even with the ratchet
        # ENABLED, a trailing PROFIT stop must not touch the stop here (that was the
        # 2026-06-29 bug: tightening a losing long, then a 1m wick whipsawed it out).
        pos = _bracket_position("long", stop=95.0)
        out = _close(pos, _RATCHET_STRATEGY, _market(99.0), feed=_feed(100.0))
        self.assertIsNone(out)                       # 99 > frozen stop 95, 15m close inside band
        self.assertEqual(pos["stop_loss_price"], 95.0)  # frozen stop untouched -> full risk room kept

    def test_long_invalidation_on_confirmed_15m_close(self):
        # The last CLOSED 15m bar closes well below the band -> thesis cut.
        out = _close(_bracket_position("long"), _TRAIL_STRATEGY, _market(95.0), feed=_feed(90.0))
        self.assertIsNotNone(out)
        self.assertEqual(out["exit_reason"], "ssl_flip")
        self.assertEqual(out["exit_price"], 95.0)    # executes at the live 1m price now

    def test_1m_wick_below_band_does_NOT_ssl_flip(self):
        # A 1m dip below the band while the closed 15m bar stays inside must never
        # produce a thesis flip (ssl_flip). With the ratchet off the frozen stop is
        # the only 1m-enforced exit; the thesis stays intact unless a 15m bar closes red.
        out = _close(_bracket_position("long", stop=90.0), _TRAIL_STRATEGY, _market(95.0), feed=_feed(100.0))
        self.assertIsNone(out)                               # no ssl_flip, frozen stop (90) not hit either

    def test_short_invalidation_on_confirmed_15m_close(self):
        out = _close(_bracket_position("short", stop=105.0), _TRAIL_STRATEGY, _market(103.0), feed=_feed(110.0))
        self.assertEqual(out["exit_reason"], "ssl_flip")

    def test_offline_no_15m_data_skips_trail_keeps_frozen_stop(self):
        # Empty 15m feed (network down) must NOT fall back to the 1m timeframe:
        # the trail is skipped and the frozen bracket stop is left untouched.
        pos = _bracket_position("long", stop=95.0)
        with patch("orum.adapters.price.recent_closed_candles", return_value=[]):
            out = close_position_if_needed(pos, _TRAIL_STRATEGY, _market(100.0), rsi=None)
        self.assertIsNone(out)
        self.assertEqual(pos["stop_loss_price"], 95.0)  # untouched -> no wrong-timeframe band

    def test_disabled_trail_falls_back_to_frozen_bracket(self):
        strat = {"risk": {"stop_loss_pct": 0.001, "take_profit_pct": 0.001, "max_hold_candles": 1, "fee_rate": 0.0,
                          "trail": {"enabled": False}}}
        pos = _bracket_position("long", stop=95.0)
        out = _close(pos, strat, _market(100.0))
        self.assertIsNone(out)
        self.assertEqual(pos["stop_loss_price"], 95.0)  # untouched


if __name__ == "__main__":
    unittest.main()
