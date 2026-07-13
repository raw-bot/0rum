import unittest
from datetime import datetime, timezone

from orum.portfolio import forecast_history
from orum.portfolio.forecast_history import (
    compose_forecast_history,
    due_realization_records,
    prediction_record,
)


class ForecastHistoryTests(unittest.TestCase):
    def setUp(self):
        self.report = {
            "active": False,
            "origin_ts": "2026-07-13T06:00:00+00:00",
            "origin_price": 100.0,
            "horizons": {
                "6": {"quantiles": {"p10": -0.02, "p50": 0.01, "p90": 0.03}},
                "12": {"quantiles": {"p10": -0.03, "p50": 0.02, "p90": 0.04}},
                "24": {"quantiles": {"p10": -0.04, "p50": 0.03, "p90": 0.05}},
            },
            "lock_reasons": ["quality lock"],
        }
        self.prediction = prediction_record(
            self.report,
            strategy_id="btc_ak_macd_4h",
            symbol="BTC/USDT",
            decision={"action": "locked", "multiplier": 1.0},
            recorded_at="2026-07-13T06:10:00+00:00",
        )

    def test_prediction_is_bucketed_to_six_hours_and_keeps_only_audit_quantiles(self):
        self.assertEqual(self.prediction["bucket_ts"], "2026-07-13T06:00:00+00:00")
        self.assertEqual(
            self.prediction["horizons"]["12"],
            {"p10": -0.03, "p50": 0.02, "p90": 0.04},
        )
        self.assertEqual(self.prediction["origin_price"], 100.0)

    def test_due_realizations_are_append_only_and_measure_median_error(self):
        candles = []
        origin = datetime(2026, 7, 13, 6, tzinfo=timezone.utc)
        closes = {0: 100.0, 6: 102.0, 12: 101.0, 24: 104.0}
        for hour in range(25):
            close = closes.get(hour, 100.0)
            candles.append({"ts": int((origin.timestamp() + hour * 3600) * 1000), "close": close})

        rows = due_realization_records(
            [self.prediction], strategy_id="btc_ak_macd_4h", candles=candles,
            recorded_at="2026-07-14T06:10:00+00:00",
        )

        self.assertEqual([row["horizon_hours"] for row in rows], [6, 12, 24])
        h12 = rows[1]
        self.assertAlmostEqual(h12["actual_return"], 0.01)
        self.assertAlmostEqual(h12["median_error"], -0.01)
        self.assertTrue(h12["direction_correct"])
        self.assertEqual(
            due_realization_records(
                [self.prediction, *rows],
                strategy_id="btc_ak_macd_4h",
                candles=candles,
                recorded_at="2026-07-14T06:15:00+00:00",
            ),
            [],
        )

    def test_composition_attaches_realizations_without_losing_prediction(self):
        actual = {
            "record_type": "realization",
            "strategy_id": "btc_ak_macd_4h",
            "bucket_ts": self.prediction["bucket_ts"],
            "horizon_hours": 6,
            "actual_price": 102.0,
            "actual_return": 0.02,
            "median_error": 0.01,
            "direction_correct": True,
        }
        history = compose_forecast_history(
            [self.prediction, actual], strategy_id="btc_ak_macd_4h"
        )

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["realized"]["6"]["actual_price"], 102.0)

    def test_realization_does_not_substitute_a_late_candle_for_missing_horizon(self):
        origin = datetime(2026, 7, 13, 6, tzinfo=timezone.utc)
        candles = [
            {"ts": int(origin.timestamp() * 1000), "close": 100.0},
            {"ts": int((origin.timestamp() + 8 * 3600) * 1000), "close": 102.0},
        ]

        rows = due_realization_records(
            [self.prediction], strategy_id="btc_ak_macd_4h", candles=candles,
            recorded_at="2026-07-13T14:10:00+00:00",
        )

        self.assertEqual(rows, [])

    def test_live_24h_realization_replaces_reconstructed_target(self):
        target_ts = "2026-07-14T06:00:00+00:00"
        realization = {
            "record_type": "realization",
            "recorded_at": "2026-07-14T06:10:00+00:00",
            "strategy_id": "btc_ak_macd_4h",
            "symbol": "BTC/USDT",
            "bucket_ts": self.prediction["bucket_ts"],
            "origin_ts": self.prediction["origin_ts"],
            "horizon_hours": 24,
            "actual_ts": target_ts,
            "actual_price": 104.0,
            "actual_return": 0.04,
            "median_return": 0.03,
            "median_error": 0.01,
            "direction_correct": True,
        }

        merged = forecast_history.merge_forecast_history_24h(
            [{
                "origin_ts": self.prediction["origin_ts"],
                "target_ts": target_ts,
                "origin_price": 100.0,
                "predicted_price": 101.0,
                "actual_price": 102.0,
                "median_return": 0.01,
                "median_error": 0.01,
                "source": "walk_forward",
            }],
            [self.prediction, realization],
            strategy_id="btc_ak_macd_4h",
        )

        self.assertEqual(merged, [{
            "origin_ts": "2026-07-13T06:00:00+00:00",
            "target_ts": target_ts,
            "origin_price": 100.0,
            "predicted_price": 103.0,
            "actual_price": 104.0,
            "median_return": 0.03,
            "median_error": 0.01,
            "source": "live_archive",
        }])


if __name__ == "__main__":
    unittest.main()
