"""Test isolation: redirect ALL state I/O to a throwaway dir.

Root cause of the live "BUY @ 100" pollution: tests construct the real
ExternalOrchestrator with its default logger (`log_event`), which appends to the
module-level `paths.EVENTS_PATH` (= live state/events.jsonl). Every `pytest` run
therefore leaked synthetic signals into the live bot's event log.

Setting HERMES_STATE_DIR here — at conftest import, BEFORE pytest imports any
test module (and thus before `hermes_trading.paths` resolves its path
constants) — points STATE_DIR / EVENTS_PATH / TRADES_PATH / ... at a tempdir, so
no test can ever touch the live state. `setdefault` lets CI override it.
"""

import os
import tempfile

os.environ.setdefault("HERMES_STATE_DIR", tempfile.mkdtemp(prefix="hermes-test-state-"))
