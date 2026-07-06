"""Tests for the AK MACD frozen SL/TP bracket + risk-based sizing + bracket exits."""

import unittest

from orum.external.bracket import bracket_sizing, compute_bracket
from orum.loop import close_position_if_needed


class TestComputeBracket(unittest.TestCase):
    def test_long_sl_is_min_of_baseline_and_recent_low(self):
        # recent_low below baseline -> SL = recent_low.
        b = compute_bracket(entry_price=100.0, baseline_at_entry=98.0, recent_low=97.0, direction="long")
        self.assertEqual(b.stop_loss_price, 97.0)
        self.assertEqual(b.sl_basis, "recent_low")
        # baseline below recent_low -> SL = baseline.
        b2 = compute_bracket(entry_price=100.0, baseline_at_entry=96.0, recent_low=97.0, direction="long")
        self.assertEqual(b2.stop_loss_price, 96.0)
        self.assertEqual(b2.sl_basis, "baseline")
        self.assertEqual(b2.stop_loss_price, min(96.0, 97.0))

    def test_long_tp_is_entry_plus_1_5_risk(self):
        b = compute_bracket(entry_price=100.0, baseline_at_entry=98.0, recent_low=97.0, direction="long")
        self.assertEqual(b.risk_distance, 3.0)
        self.assertEqual(b.take_profit_price, 100.0 + 1.5 * (100.0 - 97.0))
        self.assertEqual(b.take_profit_price, 104.5)

    def test_short_sl_is_max_of_baseline_and_recent_high(self):
        b = compute_bracket(entry_price=100.0, baseline_at_entry=102.0, recent_high=103.0, direction="short")
        self.assertEqual(b.stop_loss_price, 103.0)
        self.assertEqual(b.sl_basis, "recent_high")
        self.assertEqual(b.stop_loss_price, max(102.0, 103.0))

    def test_short_tp_is_entry_minus_1_5_risk(self):
        b = compute_bracket(entry_price=100.0, baseline_at_entry=102.0, recent_high=103.0, direction="short")
        self.assertEqual(b.risk_distance, 3.0)
        self.assertEqual(b.take_profit_price, 100.0 - 1.5 * (103.0 - 100.0))
        self.assertEqual(b.take_profit_price, 95.5)

    def test_degenerate_risk_is_refused(self):
        # SL at/above entry for a long -> non-positive risk -> ValueError.
        with self.assertRaises(ValueError):
            compute_bracket(entry_price=100.0, baseline_at_entry=100.0, recent_low=100.0, direction="long")


class TestBracketSizing(unittest.TestCase):
    def test_position_size_is_risk_usd_over_risk_distance(self):
        s = bracket_sizing(account_equity=10_000.0, risk_pct=0.005, risk_distance=3.0, entry_price=100.0)
        self.assertAlmostEqual(s["risk_usd"], 50.0)
        self.assertAlmostEqual(s["qty_base"], 50.0 / 3.0)          # position_size
        self.assertAlmostEqual(s["notional_usd"], (50.0 / 3.0) * 100.0)

    def test_wider_stop_smaller_size_constant_dollar_risk(self):
        tight = bracket_sizing(account_equity=10_000.0, risk_pct=0.005, risk_distance=3.0, entry_price=100.0)
        wide = bracket_sizing(account_equity=10_000.0, risk_pct=0.005, risk_distance=6.0, entry_price=100.0)
        self.assertLess(wide["qty_base"], tight["qty_base"])       # wider stop -> smaller size
        self.assertAlmostEqual(tight["risk_usd"], wide["risk_usd"])  # dollar risk unchanged
        # loss at SL == risk_usd for both (qty * risk_distance).
        self.assertAlmostEqual(tight["qty_base"] * 3.0, 50.0)
        self.assertAlmostEqual(wide["qty_base"] * 6.0, 50.0)

    def test_sizing_does_not_use_stop_loss_pct(self):
        # bracket_sizing has no stop_loss_pct parameter; its output depends only
        # on the real risk distance, never on a fixed %. The native %-sizing
        # (notional = risk_usd / stop_loss_pct) would give a different number.
        s = bracket_sizing(account_equity=10_000.0, risk_pct=0.005, risk_distance=3.0, entry_price=100.0)
        native_notional = (10_000.0 * 0.005) / 0.02   # risk_usd / 2% stop -> 2500
        self.assertNotAlmostEqual(s["notional_usd"], native_notional)


class TestBracketSizingGuards(unittest.TestCase):
    """Safety caps (leverage / notional) + fee-adjusted reward/risk refusal."""

    def test_normal_trade_not_capped_and_accepted(self):
        # Wide stop -> small notional, well under any cap -> untouched + accepted.
        s = bracket_sizing(
            account_equity=10_000.0, risk_pct=0.005, risk_distance=6.0, entry_price=100.0,
            max_leverage=3.0, fee_rate=0.0004, min_reward_risk=1.0,
        )
        self.assertFalse(s["capped"])
        self.assertIsNone(s["cap_reason"])
        self.assertTrue(s["accepted"])
        self.assertAlmostEqual(s["risk_usd"], 50.0)              # full target risk kept
        self.assertAlmostEqual(s["qty_base"], 50.0 / 6.0)
        self.assertAlmostEqual(s["notional_usd"], (50.0 / 6.0) * 100.0)

    def test_capped_by_max_notional(self):
        # Uncapped notional = 5000; explicit notional ceiling = 1000.
        s = bracket_sizing(
            account_equity=10_000.0, risk_pct=0.005, risk_distance=1.0, entry_price=100.0,
            max_leverage=None, max_notional_usd=1_000.0,
        )
        self.assertTrue(s["capped"])
        self.assertEqual(s["cap_reason"], "max_notional")
        self.assertAlmostEqual(s["notional_usd"], 1_000.0)
        self.assertAlmostEqual(s["qty_base"], 10.0)
        self.assertAlmostEqual(s["risk_usd"], 10.0)             # effective risk < target (50)

    def test_capped_by_max_leverage(self):
        # Uncapped notional = 40000; leverage ceiling = equity*2 = 20000.
        s = bracket_sizing(
            account_equity=10_000.0, risk_pct=0.02, risk_distance=0.5, entry_price=100.0,
            max_leverage=2.0,
        )
        self.assertTrue(s["capped"])
        self.assertEqual(s["cap_reason"], "max_leverage")
        self.assertAlmostEqual(s["notional_usd"], 20_000.0)
        self.assertAlmostEqual(s["qty_base"], 200.0)

    def test_tight_stop_that_used_to_size_an_enormous_position(self):
        # The real overnight trade: ~0.42% stop, 2% risk on ~10k equity.
        equity, risk_pct, entry, rd = 10_043.94, 0.02, 62_848.4, 261.9

        # Old behaviour (no caps): notional balloons past 48k -> ~4.8x leverage.
        uncapped = bracket_sizing(
            account_equity=equity, risk_pct=risk_pct, risk_distance=rd, entry_price=entry,
        )
        self.assertGreater(uncapped["notional_usd"], 40_000.0)
        self.assertGreater(uncapped["notional_usd"] / equity, 4.0)

        # With the leverage cap the position is bounded to 3x equity and the
        # effective dollar risk drops below the 2% target -> strictly safer.
        capped = bracket_sizing(
            account_equity=equity, risk_pct=risk_pct, risk_distance=rd, entry_price=entry,
            max_leverage=3.0,
        )
        self.assertTrue(capped["capped"])
        self.assertEqual(capped["cap_reason"], "max_leverage")
        self.assertLessEqual(capped["notional_usd"], equity * 3.0 + 1e-6)
        self.assertLess(capped["qty_base"], uncapped["qty_base"])
        self.assertLess(capped["risk_usd"], equity * risk_pct)   # < target risk_usd

    def test_trade_refused_when_real_rr_too_poor_after_cap(self):
        # A near-zero stop: fees devour the move, so the fee-adjusted reward/risk
        # collapses below the floor even though the gross (price) ratio is 1.5.
        s = bracket_sizing(
            account_equity=10_000.0, risk_pct=0.02, risk_distance=100.0, entry_price=62_848.0,
            reward_risk_ratio=1.5, max_leverage=3.0, fee_rate=0.0004, min_reward_risk=1.0,
        )
        self.assertTrue(s["capped"])                # tight stop still oversizes -> capped
        self.assertFalse(s["accepted"])             # ...then refused on real reward/risk
        self.assertLess(s["effective_reward_risk"], 1.0)
        self.assertAlmostEqual(s["gross_reward_risk"], 1.5)
        self.assertIn("real reward/risk", s["reject_reason"])


# Absurd %/hold that WOULD fire immediately if the bracket path used them.
_TRAP_STRATEGY = {"risk": {"stop_loss_pct": 0.001, "take_profit_pct": 0.001, "max_hold_candles": 1, "fee_rate": 0.0}}


def _bracket_position(direction="long"):
    return {
        "exit_mode": "bracket", "direction": direction, "entry_price": 100.0,
        "stop_loss_price": 97.0 if direction == "long" else 103.0,
        "take_profit_price": 104.5 if direction == "long" else 95.5,
        "notional_usd": 1000.0, "qty_base": 10.0, "risk_usd": 50.0,
        "opened_candle_ts": 0, "candle_interval_ms": 900_000,
    }


def _market(price, ts=10**12):
    return {"closes": [price], "last_candle_ts": ts, "source": "tradingview"}


class TestBracketExit(unittest.TestCase):
    def test_long_no_exit_between_sl_and_tp_ignores_pct_and_maxhold(self):
        # Price sits between SL and TP; trap %/hold would close immediately if used.
        out = close_position_if_needed(_bracket_position("long"), _TRAP_STRATEGY, _market(100.0), rsi=None)
        self.assertIsNone(out)

    def test_long_closes_at_exact_sl(self):
        out = close_position_if_needed(_bracket_position("long"), _TRAP_STRATEGY, _market(97.0), rsi=None)
        self.assertIsNotNone(out)
        self.assertEqual(out["exit_reason"], "stop_loss")
        self.assertEqual(out["exit_price"], 97.0)

    def test_long_closes_at_exact_tp(self):
        out = close_position_if_needed(_bracket_position("long"), _TRAP_STRATEGY, _market(104.5), rsi=None)
        self.assertIsNotNone(out)
        self.assertEqual(out["exit_reason"], "take_profit")

    def test_short_closes_at_exact_sl_and_tp(self):
        sl = close_position_if_needed(_bracket_position("short"), _TRAP_STRATEGY, _market(103.0), rsi=None)
        self.assertEqual(sl["exit_reason"], "stop_loss")
        tp = close_position_if_needed(_bracket_position("short"), _TRAP_STRATEGY, _market(95.5), rsi=None)
        self.assertEqual(tp["exit_reason"], "take_profit")

    def test_short_pnl_is_direction_aware(self):
        # Short stopped out at 103 from entry 100 -> a 3% LOSS, not a gain.
        out = close_position_if_needed(_bracket_position("short"), _TRAP_STRATEGY, _market(103.0), rsi=None)
        self.assertAlmostEqual(out["pnl_pct"], -0.03)

    def test_overshoot_fills_at_level_not_close_long(self):
        # A fast bar closes WELL past the SL (95 << 97). The fill must still be the
        # frozen SL (97.0), so the realized loss matches the at-level stop (-3%),
        # NOT the bigger -5% the overshooting close (95.0) would have booked.
        out = close_position_if_needed(_bracket_position("long"), _TRAP_STRATEGY, _market(95.0), rsi=None)
        self.assertEqual(out["exit_reason"], "stop_loss")
        self.assertEqual(out["exit_price"], 97.0)        # level, not 95.0 close
        self.assertAlmostEqual(out["pnl_pct"], -0.03)    # (97-100)/100, not -0.05

    def test_overshoot_fills_at_level_not_close_short(self):
        # Mirror of the live bug: a short whose stop bar closes past 103 (here 106)
        # must fill at 103.0 (-3%), not at 106.0 (-6%) -- this is what produced the
        # -1.64R instead of -1R loss on the 2026-06-21 live short.
        out = close_position_if_needed(_bracket_position("short"), _TRAP_STRATEGY, _market(106.0), rsi=None)
        self.assertEqual(out["exit_reason"], "stop_loss")
        self.assertEqual(out["exit_price"], 103.0)       # level, not 106.0 close
        self.assertAlmostEqual(out["pnl_pct"], -0.03)

    def test_overshoot_tp_fills_at_level_long(self):
        # A gap-through TP (close 110 > tp 104.5) fills at the TP level, no windfall.
        out = close_position_if_needed(_bracket_position("long"), _TRAP_STRATEGY, _market(110.0), rsi=None)
        self.assertEqual(out["exit_reason"], "take_profit")
        self.assertEqual(out["exit_price"], 104.5)       # level, not 110.0 close
        self.assertAlmostEqual(out["pnl_pct"], 0.045)


if __name__ == "__main__":
    unittest.main()
