from __future__ import annotations

import unittest

from orum.portfolio.position_manager import (
    GENERIC_POLICY_VERSION,
    DynamicExitPolicy,
    DynamicExitState,
    ExitAction,
    evaluate_dynamic_exit,
    evaluate_ak_exit,
)


def _candles(closes: list[float]) -> list[dict]:
    return [
        {
            "ts": index,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1.0,
        }
        for index, close in enumerate(closes)
    ]


class PositionManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = DynamicExitPolicy(
            mode="execute", ema_len=3, atr_len=2, ssl_atr_mult=1.0
        )
        self.state = DynamicExitState.initial(
            self.policy, entry_price=100.0, last_candle_ts=0
        )

    def test_does_not_arm_below_one_official_atr_r(self):
        candles = _candles([100.0, 103.0, 108.0])
        result = evaluate_ak_exit(
            position_id="ak",
            entry_price=100.0,
            atr_risk=10.0,
            initial_stop_price=85.0,
            previous_state=self.state,
            candle=candles[-1],
            candles_asof=candles,
        )
        self.assertFalse(result.next_state.armed)
        self.assertIsNone(result.next_state.dynamic_stop_price)
        self.assertEqual(result.decision.action, ExitAction.HOLD)

    def test_arms_and_computes_stop_for_next_candle(self):
        candles = _candles([100.0, 105.0, 112.0])
        result = evaluate_ak_exit(
            position_id="ak",
            entry_price=100.0,
            atr_risk=10.0,
            initial_stop_price=85.0,
            previous_state=self.state,
            candle=candles[-1],
            candles_asof=candles,
        )
        self.assertTrue(result.next_state.armed)
        self.assertGreaterEqual(result.next_state.dynamic_stop_price, 107.0)
        self.assertEqual(result.decision.action, ExitAction.HOLD)

    def test_stop_never_moves_backwards(self):
        previous = DynamicExitState(
            policy=self.policy,
            peak_favorable_price=120.0,
            mfe_r=2.0,
            dynamic_stop_price=114.0,
            armed=True,
            last_candle_ts=2,
        )
        candles = _candles([111.0, 112.0, 116.0])
        result = evaluate_ak_exit(
            position_id="ak",
            entry_price=100.0,
            atr_risk=10.0,
            initial_stop_price=85.0,
            previous_state=previous,
            candle=candles[-1],
            candles_asof=candles,
        )
        self.assertGreaterEqual(result.next_state.dynamic_stop_price, 114.0)

    def test_candidate_above_close_is_market_close_not_retroactive_stop(self):
        previous = DynamicExitState(
            policy=self.policy,
            peak_favorable_price=120.0,
            mfe_r=2.0,
            dynamic_stop_price=110.0,
            armed=True,
            last_candle_ts=2,
        )
        candles = _candles([115.0, 114.0, 112.0])
        result = evaluate_ak_exit(
            position_id="ak",
            entry_price=100.0,
            atr_risk=10.0,
            initial_stop_price=85.0,
            previous_state=previous,
            candle=candles[-1],
            candles_asof=candles,
        )
        self.assertEqual(result.decision.action, ExitAction.CLOSE)
        self.assertEqual(result.decision.reason_code, "dynamic_profit_reversal")
        self.assertEqual(result.decision.desired_price, 112.0)

    def test_observe_close_is_latched_as_hypothetical_only(self):
        observe = DynamicExitPolicy(
            mode="observe", ema_len=3, atr_len=2, ssl_atr_mult=1.0
        )
        previous = DynamicExitState(
            policy=observe,
            peak_favorable_price=120.0,
            mfe_r=2.0,
            dynamic_stop_price=110.0,
            armed=True,
            last_candle_ts=2,
        )
        candles = _candles([115.0, 114.0, 112.0])

        result = evaluate_ak_exit(
            position_id="ak",
            entry_price=100.0,
            atr_risk=10.0,
            initial_stop_price=85.0,
            previous_state=previous,
            candle=candles[-1],
            candles_asof=candles,
        )

        self.assertEqual(result.decision.action, ExitAction.CLOSE)
        self.assertTrue(result.next_state.hypothetical_closed)
        self.assertEqual(
            result.next_state.hypothetical_exit_reason,
            "dynamic_profit_reversal",
        )

    def test_generic_policy_has_no_ssl_execution_path(self):
        policy = DynamicExitPolicy(
            mode="execute",
            version=GENERIC_POLICY_VERSION,
            activation_r=1.0,
            giveback_r=0.5,
            floor_r=0.1,
            ema_len=3,
            atr_len=2,
            ssl_atr_mult=1.0,
            close_on_ssl_invalidation=False,
        )
        previous = DynamicExitState(
            policy=policy,
            peak_favorable_price=210.0,
            mfe_r=1.1,
            dynamic_stop_price=150.0,
            armed=True,
            last_candle_ts=2,
        )
        candles = _candles([210.0, 210.0, 190.0])

        result = evaluate_dynamic_exit(
            position_id="ut",
            entry_price=100.0,
            atr_risk=100.0,
            initial_stop_price=80.0,
            previous_state=previous,
            candle=candles[-1],
            candles_asof=candles,
        )

        self.assertEqual(result.decision.action, ExitAction.HOLD)
        self.assertEqual(result.next_state.dynamic_stop_price, 160.0)

    def test_generic_config_defaults_ssl_off(self):
        policy = DynamicExitPolicy.from_config({
            "mode": "execute",
            "version": GENERIC_POLICY_VERSION,
        })

        self.assertIsNotNone(policy)
        self.assertFalse(policy.close_on_ssl_invalidation)

    def test_adoption_epoch_is_persisted(self):
        policy = DynamicExitPolicy.from_config({
            "mode": "execute",
            "version": GENERIC_POLICY_VERSION,
        })
        state = DynamicExitState.initial(
            policy,
            entry_price=100.0,
            last_candle_ts=42,
            tracking_origin="adoption",
        )

        restored = DynamicExitState.from_dict(state.to_dict())
        self.assertEqual(restored.tracking_origin, "adoption")
        self.assertEqual(restored.tracking_started_at, 42)

    def test_legacy_ak_state_with_old_floor_shape_remains_loadable(self):
        raw = self.state.to_dict()
        raw["policy"]["activation_r"] = 1.0
        raw["policy"]["floor_r"] = 2.0

        restored = DynamicExitState.from_dict(raw)

        self.assertEqual(restored.policy.version, "ak_mfe_ssl_v1")
        self.assertEqual(restored.policy.floor_r, 2.0)

    def test_new_config_rejects_floor_above_activation(self):
        with self.assertRaisesRegex(ValueError, "floor_r"):
            DynamicExitPolicy.from_config({
                "mode": "execute",
                "version": "ak_mfe_ssl_v1",
                "activation_r": 1.0,
                "floor_r": 2.0,
            })

    def test_strong_trend_extends_active_target_for_next_candle(self):
        policy = DynamicExitPolicy(
            mode="execute", ema_len=4, atr_len=2, adaptive_target=True,
            target_review_buffer_r=0.5, target_step_r=1.0,
            target_strength_min=3,
        )
        previous = DynamicExitState.initial(
            policy, entry_price=100.0, last_candle_ts=0,
            initial_target_price=120.0, atr_risk=10.0,
        )
        candles = _candles([100.0, 102.0, 104.0, 108.0, 112.0, 116.0])
        result = evaluate_dynamic_exit(
            position_id="ak", entry_price=100.0, atr_risk=10.0,
            initial_stop_price=90.0, initial_target_price=120.0,
            previous_state=previous, candle=candles[-1],
            candles_asof=candles,
        )
        self.assertEqual(result.decision.action, ExitAction.HOLD)
        self.assertEqual(result.decision.reason_code, "dynamic_target_extended")
        self.assertEqual(result.next_state.active_target_r, 3.0)
        self.assertEqual(result.next_state.active_target_price, 130.0)
        self.assertEqual(result.next_state.target_extensions, 1)
        self.assertGreaterEqual(result.next_state.target_strength_score, 3)

    def test_weak_trend_keeps_current_target_executable(self):
        policy = DynamicExitPolicy(
            mode="execute", ema_len=4, atr_len=2, adaptive_target=True,
            target_review_buffer_r=0.5, target_step_r=1.0,
            target_strength_min=3,
        )
        initial = DynamicExitState.initial(
            policy, entry_price=100.0, last_candle_ts=0,
            initial_target_price=120.0, atr_risk=10.0,
        )
        previous = DynamicExitState(**{
            **initial.__dict__, "peak_favorable_price": 116.0, "mfe_r": 1.6,
        })
        candles = _candles([100.0, 110.0, 116.0, 114.0, 112.0, 110.0])
        result = evaluate_dynamic_exit(
            position_id="ak", entry_price=100.0, atr_risk=10.0,
            initial_stop_price=90.0, initial_target_price=120.0,
            previous_state=previous, candle=candles[-1],
            candles_asof=candles,
        )
        self.assertEqual(result.decision.action, ExitAction.CLOSE)
        self.assertEqual(result.decision.reason_code, "dynamic_profit_reversal")
        self.assertEqual(result.next_state.active_target_price, 120.0)
        self.assertEqual(result.next_state.target_extensions, 0)
        self.assertLess(result.next_state.target_strength_score, 3)

    def test_adaptive_target_state_roundtrips(self):
        policy = DynamicExitPolicy(mode="execute", adaptive_target=True)
        state = DynamicExitState.initial(
            policy, entry_price=100.0, last_candle_ts=7,
            initial_target_price=120.0, atr_risk=10.0,
        )
        restored = DynamicExitState.from_dict(state.to_dict())
        self.assertEqual(restored.active_target_price, 120.0)
        self.assertEqual(restored.active_target_r, 2.0)
        self.assertEqual(restored.target_extensions, 0)

    def test_adaptive_target_config_is_opt_in_and_validated(self):
        legacy = DynamicExitPolicy.from_config({
            "mode": "execute", "version": "ak_mfe_ssl_v1",
        })
        self.assertFalse(legacy.adaptive_target)
        with self.assertRaisesRegex(ValueError, "target_step_r"):
            DynamicExitPolicy.from_config({
                "mode": "execute", "version": "ak_mfe_ssl_v1",
                "adaptive_target": True, "target_step_r": 0,
            })
        with self.assertRaisesRegex(ValueError, "target_strength_min"):
            DynamicExitPolicy.from_config({
                "mode": "execute", "version": "ak_mfe_ssl_v1",
                "adaptive_target": True, "target_strength_min": True,
            })


if __name__ == "__main__":
    unittest.main()
