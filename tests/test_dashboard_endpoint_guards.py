import unittest

from orum.dashboard import REFLECT_MIN_INTERVAL_SECONDS, origin_allowed, reflect_allowed


class OriginGuardTests(unittest.TestCase):
    def test_no_origin_header_is_allowed(self):
        self.assertTrue(origin_allowed(None, "127.0.0.1:8787"))

    def test_same_origin_is_allowed(self):
        self.assertTrue(origin_allowed("http://127.0.0.1:8787", "127.0.0.1:8787"))

    def test_cross_origin_browser_request_is_rejected(self):
        self.assertFalse(origin_allowed("https://evil.example", "127.0.0.1:8787"))
        self.assertFalse(origin_allowed("http://localhost:9999", "127.0.0.1:8787"))


class ReflectThrottleTests(unittest.TestCase):
    def test_first_trigger_is_allowed(self):
        self.assertTrue(reflect_allowed(now=1000.0, last_ts=0.0))

    def test_rapid_second_trigger_is_blocked(self):
        self.assertFalse(reflect_allowed(now=1000.0 + REFLECT_MIN_INTERVAL_SECONDS - 1, last_ts=1000.0))

    def test_trigger_after_interval_is_allowed(self):
        self.assertTrue(reflect_allowed(now=1000.0 + REFLECT_MIN_INTERVAL_SECONDS, last_ts=1000.0))


if __name__ == "__main__":
    unittest.main()
