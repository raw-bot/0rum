"""Unit tests for the AK MACD brain + the symmetric LONG/SHORT candidate-confirmation
lifecycle (specs/ak-macd-long-entry-confirmation.md + ak-macd-short-entry-confirmation.md).

Lifecycle tests build an AkMacdState directly so the MACD values are exactly
controlled. LONG and SHORT are exact mirrors: flip_up arms a long candidate
(confirm on strictly-increasing MACD, blocked when regime unfavorable), flip_down
arms a short candidate (confirm on strictly-decreasing MACD, blocked when regime
favorable); an opposite flip switches sides.
"""

import tempfile
import unittest
from pathlib import Path

from hermes_trading.external.ak_macd import (
    ACTION_ARMED,
    ACTION_CONFIRMED,
    ACTION_EXPIRED,
    ACTION_REJECTED_CONDITIONS,
    ACTION_REJECTED_MACD,
    ACTION_REJECTED_REGIME,
    AkMacdParams,
    AkMacdState,
    run_candidate_machine,
)
from hermes_trading.external.ak_macd_producer import load_ak_macd_params
from hermes_trading.external.signal import parse_external_signal
from hermes_trading.external.validate import ValidationContext, validate_external_signal

BAR_MS = 900_000
START_TS = 1_781_424_000_000
NAN = float("nan")


def _timestamps(n):
    return [START_TS + i * BAR_MS for i in range(n)]


def _state(macd, *, closes=None, opens=None, baseline=90.0, vol_ma=1.0, volume=2.0,
           trend_blue=True, pullback_at=(13, 14)):
    """Build a state of len(macd). With trend_blue=True the structural gate is a
    blue trend + gray pullback (sequenced_long); trend_blue=False gives a red trend
    + gray pullback (sequenced_short). Defaults satisfy volume>vol_ma.

    opens default to NaN so the entry-candle direction gate is inert (passes) for
    tests that don't exercise it; pass explicit opens to test that gate."""
    n = len(macd)
    closes = closes if closes is not None else [100.0] * n
    opens_l = opens if opens is not None else [NAN] * n
    baseline_l = baseline if isinstance(baseline, list) else [baseline] * n
    vol_ma_l = vol_ma if isinstance(vol_ma, list) else [vol_ma] * n
    volumes = volume if isinstance(volume, list) else [volume] * n
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    is_blue = [trend_blue] * n
    is_red = [not trend_blue] * n
    is_gray = [False] * n
    for g in pullback_at:
        if 0 <= g < n:
            is_blue[g] = False
            is_red[g] = False
            is_gray[g] = True
    return AkMacdState(
        closes=closes, opens=opens_l, highs=highs, lows=lows, volumes=volumes,
        baseline=baseline_l, macd=list(macd), signal=[NAN] * n, vol_ma=vol_ma_l,
        is_blue=is_blue, is_red=is_red, is_gray=is_gray,
    )


def _run(state, params=None, **kw):
    params = params or AkMacdParams()
    n = len(state.macd)
    return run_candidate_machine(state, _timestamps(n), params, symbol="BTCUSD", **kw)


# Strictly-decreasing MACD head (no spurious flip) for a LONG pattern tail (>0).
def _macd(*tail, head_from=400, head_to=250):
    return list(range(head_from, head_to - 1, -10)) + list(tail)


# Strictly-increasing (negative) MACD head for a SHORT pattern tail (<0).
def _macd_dn(*tail, head_from=-400, head_to=-250):
    return list(range(head_from, head_to + 1, 10)) + list(tail)


def _falling_closes(n):
    return [120.0 - 0.5 * i for i in range(n)]   # rolling return clearly negative


def _rising_closes(n):
    return [100.0 + 0.5 * i for i in range(n)]   # rolling return clearly positive


class AkMacdLongTest(unittest.TestCase):
    def test_flip_up_arms_only_no_entry(self):
        v = _run(_state(_macd(232, 176, 138, 157)))
        self.assertEqual(v.action, ACTION_ARMED)
        self.assertEqual(v.side, "long")
        self.assertIsNone(v.payload)

    def test_232_176_138_157_then_higher_confirms(self):
        self.assertEqual(_run(_state(_macd(232, 176, 138, 157))).action, ACTION_ARMED)
        v = _run(_state(_macd(232, 176, 138, 157, 170)))
        self.assertEqual(v.action, ACTION_CONFIRMED)
        self.assertEqual(v.payload["event"], "BUY_CANDIDATE")
        self.assertEqual(v.macd[0], 170.0)

    def test_sharp_drop_after_long_switches_to_short(self):
        # The real losing trade (157 then 129): no LONG entry; the flip_down arms a
        # SHORT candidate instead (symmetric switch).
        v = _run(_state(_macd(232, 176, 138, 157, 129)))
        self.assertIsNone(v.payload)          # no long entry — the -230 trade is prevented
        self.assertEqual(v.action, ACTION_ARMED)
        self.assertEqual(v.side, "short")

    def test_long_expires_on_macd_stall(self):
        # A tie (157 -> 157) is not a flip_down, so the long candidate expires.
        v = _run(_state(_macd(232, 176, 138, 157, 157)))
        self.assertEqual(v.action, ACTION_EXPIRED)
        self.assertEqual(v.side, "long")

    def test_window_expiry_when_regime_blocks_throughout(self):
        macd = _macd(232, 176, 138, 157, 165, 175, 185)
        v = _run(_state(macd, closes=_falling_closes(len(macd)), baseline=10.0))
        self.assertEqual(v.action, ACTION_EXPIRED)
        self.assertIsNone(v.payload)

    def test_regime_blocks_then_recovers_within_window(self):
        macd = _macd(232, 176, 138, 157, 165, 175)
        n = len(macd)
        closes = [120.0 - 0.5 * i for i in range(n)]
        closes[-1] = 400.0  # jump -> favorable rolling return at the confirm bar
        v = _run(_state(macd, closes=closes, baseline=10.0))
        self.assertEqual(v.action, ACTION_CONFIRMED)
        self.assertEqual(v.payload["event"], "BUY_CANDIDATE")

    def test_new_flip_rearms(self):
        v = _run(_state(_macd(232, 176, 138, 157, 150, 140, 155)))
        self.assertEqual(v.action, ACTION_ARMED)
        self.assertEqual(v.remaining_window, 2)

    def test_rejected_macd_not_rising_with_confirmation_3(self):
        params = AkMacdParams(confirmation_bars=3, candidate_window_bars=5)
        v = _run(_state(_macd(232, 176, 138, 157, 165)), params)
        self.assertEqual(v.action, ACTION_REJECTED_MACD)
        self.assertIsNone(v.payload)

    # --- entry-candle direction gate (require_candle_direction) ---
    def test_green_entry_candle_confirms_long(self):
        # Same confirming pattern, entry bar GREEN (close 100 > open 99) -> long fires.
        m = _macd(232, 176, 138, 157, 170)
        opens = [NAN] * (len(m) - 1) + [99.0]
        v = _run(_state(m, opens=opens))
        self.assertEqual(v.action, ACTION_CONFIRMED)
        self.assertEqual(v.payload["event"], "BUY_CANDIDATE")

    def test_red_entry_candle_blocks_long(self):
        # The 2026-06-24 failure: indicators say long but the entry candle is RED
        # (close 100 < open 101) -> the gate rejects it.
        m = _macd(232, 176, 138, 157, 170)
        opens = [NAN] * (len(m) - 1) + [101.0]
        v = _run(_state(m, opens=opens))
        self.assertEqual(v.action, ACTION_REJECTED_CONDITIONS)
        self.assertIsNone(v.payload)

    def test_red_entry_candle_allowed_when_gate_disabled(self):
        m = _macd(232, 176, 138, 157, 170)
        opens = [NAN] * (len(m) - 1) + [101.0]
        v = _run(_state(m, opens=opens), AkMacdParams(require_candle_direction=False))
        self.assertEqual(v.action, ACTION_CONFIRMED)
        self.assertEqual(v.payload["event"], "BUY_CANDIDATE")


class AkMacdShortTest(unittest.TestCase):
    def _short_state(self, macd, **kw):
        kw.setdefault("closes", [70.0] * len(macd))
        kw.setdefault("baseline", 90.0)
        kw.setdefault("trend_blue", False)  # red trend for sequenced_short
        return _state(macd, **kw)

    def test_flip_down_arms_only_no_entry(self):
        v = _run(self._short_state(_macd_dn(-232, -176, -138, -157)))
        self.assertEqual(v.action, ACTION_ARMED)
        self.assertEqual(v.side, "short")
        self.assertIsNone(v.payload)

    def test_sustained_decrease_confirms_sell(self):
        self.assertEqual(_run(self._short_state(_macd_dn(-232, -176, -138, -157))).action, ACTION_ARMED)
        v = _run(self._short_state(_macd_dn(-232, -176, -138, -157, -170)))
        self.assertEqual(v.action, ACTION_CONFIRMED)
        self.assertEqual(v.side, "short")
        self.assertEqual(v.payload["event"], "SELL_CANDIDATE")

    def test_false_breakdown_does_not_enter_short(self):
        # flip_down then MACD ticks back up -> no short entry (switches to long).
        v = _run(self._short_state(_macd_dn(-232, -176, -138, -157, -129)))
        self.assertIsNone(v.payload)
        self.assertEqual(v.side, "long")  # flip_up switched the candidate

    def test_red_entry_candle_confirms_short(self):
        # Entry bar RED (close 70 < open 71) -> short fires.
        m = _macd_dn(-232, -176, -138, -157, -170)
        opens = [NAN] * (len(m) - 1) + [71.0]
        v = _run(self._short_state(m, opens=opens))
        self.assertEqual(v.action, ACTION_CONFIRMED)
        self.assertEqual(v.payload["event"], "SELL_CANDIDATE")

    def test_green_entry_candle_blocks_short(self):
        # Indicators say short but the entry candle is GREEN (close 70 > open 69) -> rejected.
        m = _macd_dn(-232, -176, -138, -157, -170)
        opens = [NAN] * (len(m) - 1) + [69.0]
        v = _run(self._short_state(m, opens=opens))
        self.assertEqual(v.action, ACTION_REJECTED_CONDITIONS)
        self.assertIsNone(v.payload)

    def test_regime_favorable_blocks_short(self):
        macd = _macd_dn(-232, -176, -138, -157, -170)
        v = _run(self._short_state(macd, closes=_rising_closes(len(macd)), baseline=300.0))
        self.assertEqual(v.action, ACTION_REJECTED_REGIME)
        self.assertEqual(v.regime, "favorable")
        self.assertIsNone(v.payload)

    def test_regime_favorable_then_clears_confirms_short(self):
        # favorable at the first confirm bar (rejected_regime, kept), then the
        # regime clears within the window while MACD still falling -> confirmed.
        macd = _macd_dn(-232, -176, -138, -157, -165, -175)
        n = len(macd)
        closes = [80.0 + 0.5 * i for i in range(n)]  # rising -> favorable
        closes[-1] = 10.0                            # drop -> no longer favorable at confirm bar
        v = _run(self._short_state(macd, closes=closes, baseline=300.0))
        self.assertEqual(v.action, ACTION_CONFIRMED)
        self.assertEqual(v.payload["event"], "SELL_CANDIDATE")

    def test_short_window_expiry_when_regime_blocks_throughout(self):
        macd = _macd_dn(-232, -176, -138, -157, -170, -180, -190)
        v = _run(self._short_state(macd, closes=_rising_closes(len(macd)), baseline=300.0))
        self.assertEqual(v.action, ACTION_EXPIRED)
        self.assertIsNone(v.payload)

    def test_allow_short_false_arms_no_short(self):
        macd = _macd_dn(-232, -176, -138, -157, -170)
        v = _run(self._short_state(macd), AkMacdParams(allow_short=False))
        self.assertNotEqual(v.side, "short")
        self.assertIsNone(v.payload)


class AkMacdLoggingTest(unittest.TestCase):
    def test_candidate_armed_carries_regime_and_full_window(self):
        v = _run(_state(_macd(232, 176, 138, 157)))
        self.assertEqual(v.action, ACTION_ARMED)
        self.assertIsNotNone(v.regime)
        self.assertIsNotNone(v.rolling_return)
        self.assertEqual(v.remaining_window, 2)
        self.assertEqual(v.macd[0], 157.0)
        self.assertEqual(v.side, "long")

    def test_candidate_confirmed_reports_real_remaining_window(self):
        v = _run(_state(_macd(232, 176, 138, 157, 170)))
        self.assertEqual(v.action, ACTION_CONFIRMED)
        self.assertIsNotNone(v.remaining_window)
        self.assertIsNotNone(v.regime)
        self.assertIsNotNone(v.rolling_return)

    def test_candidate_expired_carries_regime_and_window(self):
        macd = _macd(232, 176, 138, 157, 165, 175, 185)
        v = _run(_state(macd, closes=_falling_closes(len(macd)), baseline=10.0))
        self.assertEqual(v.action, ACTION_EXPIRED)
        self.assertIsNotNone(v.regime)
        self.assertIsNotNone(v.rolling_return)
        self.assertIsNotNone(v.remaining_window)

    def test_rejected_macd_carries_regime(self):
        params = AkMacdParams(confirmation_bars=3, candidate_window_bars=5)
        v = _run(_state(_macd(232, 176, 138, 157, 165)), params)
        self.assertEqual(v.action, ACTION_REJECTED_MACD)
        self.assertIsNotNone(v.regime)
        self.assertIsNotNone(v.rolling_return)
        self.assertIsNotNone(v.remaining_window)


class AkMacdConfigTest(unittest.TestCase):
    def test_defaults_when_no_section(self):
        path = Path(tempfile.mkdtemp()) / "strategy.yaml"
        path.write_text("risk:\n  position_size_r: 2.0\n")
        params = load_ak_macd_params(path)
        self.assertEqual(params.confirmation_bars, 2)
        self.assertEqual(params.candidate_window_bars, 2)
        self.assertTrue(params.regime_filter)

    def test_override_from_strategy_yaml(self):
        path = Path(tempfile.mkdtemp()) / "strategy.yaml"
        path.write_text("ak_macd:\n  confirmation_bars: 3\n  candidate_window_bars: 5\n  regime_filter: false\n")
        params = load_ak_macd_params(path)
        self.assertEqual(params.confirmation_bars, 3)
        self.assertEqual(params.candidate_window_bars, 5)
        self.assertFalse(params.regime_filter)

    def test_invalid_values_fall_back_to_default(self):
        path = Path(tempfile.mkdtemp()) / "strategy.yaml"
        path.write_text("ak_macd:\n  confirmation_bars: 0\n  candidate_window_bars: -2\n  regime_filter: maybe\n")
        params = load_ak_macd_params(path)
        self.assertEqual(params.confirmation_bars, 2)
        self.assertEqual(params.candidate_window_bars, 2)
        self.assertTrue(params.regime_filter)


class AkMacdPipelineTest(unittest.TestCase):
    def _validate(self, payload):
        signal = parse_external_signal(payload)
        goal = {
            "allowed_external_sources": ["local"],
            "allowed_external_strategies": [
                {"id": "ak_macd_15m_v1", "symbol": "BTCUSD", "timeframe": "15m",
                 "events": ["BUY_CANDIDATE", "SELL_CANDIDATE", "EXIT"]},
            ],
            "allow_short": True,
        }
        ctx = ValidationContext(
            goal=goal, open_position=None, recent_trades=(), resume_ack=False,
            trading_mode="paper", price_offline=False, now_ms=signal.bar_time + BAR_MS + 1,
        )
        return validate_external_signal(signal, ctx)

    def test_confirmed_long_payload_passes_validate(self):
        v = _run(_state(_macd(232, 176, 138, 157, 170)))
        self.assertTrue(self._validate(v.payload).accepted)

    def test_confirmed_short_payload_passes_validate(self):
        v = _run(_state(_macd_dn(-232, -176, -138, -157, -170),
                        baseline=200.0, trend_blue=False))  # default closes (100) < baseline
        self.assertEqual(v.payload["event"], "SELL_CANDIDATE")
        self.assertTrue(self._validate(v.payload).accepted)


if __name__ == "__main__":
    unittest.main()
