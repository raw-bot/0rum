import unittest

from hermes_trading.hermes_watch import watcher_status


class HermesWatchTests(unittest.TestCase):
    def test_watcher_status_explains_connection_state(self):
        status = watcher_status(
            status="standby",
            reflection_every=10,
            trades_seen=6,
            trades_since_reflection=1,
            detail="Waiting for 9 more closed trades before Hermes reflection.",
        )

        self.assertEqual(status["status"], "standby")
        self.assertEqual(status["mode"], "local_hermes_watcher")
        self.assertEqual(status["reflection_every"], 10)
        self.assertIn("Waiting", status["detail"])
