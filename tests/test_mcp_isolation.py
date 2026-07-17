"""orum_mcp is a strictly additive module: the bot never imports it and keeps
working when it is absent."""

import os
import subprocess
import sys
import tempfile
import unittest

from orum.paths import PROJECT_ROOT

BOT_IMPORTS_WITHOUT_MCP = r"""
import sys
from importlib.abc import MetaPathFinder

class BlockOrumMcp(MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "orum_mcp" or name.startswith("orum_mcp."):
            raise ImportError("orum_mcp removed")

sys.meta_path.insert(0, BlockOrumMcp())
import orum.loop
import orum.dashboard
import orum.run
import orum.orum_watch
import orum.portfolio.paper_engine
print("bot-imports-ok")
"""


class IsolationTests(unittest.TestCase):
    def test_nothing_under_orum_references_orum_mcp(self):
        offenders = [
            str(path)
            for path in (PROJECT_ROOT / "orum").rglob("*.py")
            if "orum_mcp" in path.read_text()
        ]
        self.assertEqual(offenders, [])

    def test_bot_imports_cleanly_with_orum_mcp_unimportable(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "0RUM_STATE_DIR": tmp}
            proc = subprocess.run(
                [sys.executable, "-c", BOT_IMPORTS_WITHOUT_MCP],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("bot-imports-ok", proc.stdout)


if __name__ == "__main__":
    unittest.main()
