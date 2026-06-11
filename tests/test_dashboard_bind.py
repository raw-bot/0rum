import unittest
from unittest.mock import patch

from hermes_trading.dashboard import _bind_address


class DashboardBindTests(unittest.TestCase):
    def test_defaults_to_localhost_8787(self):
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("HERMES_DASHBOARD_HOST", None)
            os.environ.pop("HERMES_DASHBOARD_PORT", None)
            self.assertEqual(_bind_address(), ("127.0.0.1", 8787))

    def test_env_overrides_host_and_port(self):
        with patch.dict("os.environ", {"HERMES_DASHBOARD_HOST": "0.0.0.0", "HERMES_DASHBOARD_PORT": "9000"}):
            self.assertEqual(_bind_address(), ("0.0.0.0", 9000))

    def test_invalid_port_falls_back_to_default(self):
        with patch.dict("os.environ", {"HERMES_DASHBOARD_PORT": "not-a-port"}):
            self.assertEqual(_bind_address()[1], 8787)


if __name__ == "__main__":
    unittest.main()
