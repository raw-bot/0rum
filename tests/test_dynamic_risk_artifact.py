import unittest

from scripts.build_dynamic_risk_shadow_artifact import build_artifact


def _trade(position_id, *, ratio, pnl):
    entry = 100.0
    atr_risk = 5.0
    stop = entry - ratio * atr_risk
    return [
        {
            "ts": "2026-01-01T00:00:00+00:00",
            "position_id": position_id,
            "strategy_id": "btc_utbot_m15_h1",
            "action": "open",
            "price": entry,
            "qty": 1.0,
            "atr_risk": atr_risk,
            "stop_loss_price": stop,
            "fee_usd": 0.1,
        },
        {
            "ts": "2026-01-02T00:00:00+00:00",
            "position_id": position_id,
            "strategy_id": "btc_utbot_m15_h1",
            "action": "close",
            "realized_pnl_usd": pnl + 0.1,
        },
    ]


class DynamicRiskArtifactTests(unittest.TestCase):
    def test_builder_groups_completed_episodes_and_stays_research_only(self):
        fills = [
            *_trade("a", ratio=2.5, pnl=10.0),
            *_trade("b", ratio=2.8, pnl=-5.0),
            *_trade("c", ratio=3.5, pnl=20.0),
            *_trade("d", ratio=4.0, pnl=-10.0),
            {
                "ts": "2026-01-03T00:00:00+00:00",
                "position_id": "still-open",
                "strategy_id": "btc_utbot_m15_h1",
                "action": "open",
                "price": 100.0,
                "qty": 1.0,
                "atr_risk": 5.0,
                "stop_loss_price": 80.0,
                "fee_usd": 0.1,
            },
        ]

        artifact = build_artifact(
            fills,
            source_sha256="fills-hash",
            data_fingerprint="data-hash",
            config_sha256="config-hash",
        )

        self.assertEqual(artifact["status"], "research_only")
        self.assertFalse(artifact["promotion_eligible"])
        self.assertEqual(artifact["completed_episodes"], 4)
        self.assertEqual(sum(row["sample_n"] for row in artifact["buckets"]), 4)
        self.assertEqual(artifact["source"]["fills_sha256"], "fills-hash")
        self.assertEqual(artifact["label"], "net_pnl_after_both_fees_gt_zero")


if __name__ == "__main__":
    unittest.main()
