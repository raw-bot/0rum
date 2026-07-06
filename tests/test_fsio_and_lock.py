import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from orum import reflect
from orum.fsio import atomic_write_json, atomic_write_text


class AtomicWriteTests(unittest.TestCase):
    def test_writes_content_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "strategy.yaml"

            atomic_write_text(target, "version: '01'\n")

            self.assertEqual(target.read_text(), "version: '01'\n")
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["strategy.yaml"])

    def test_overwrites_existing_file_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "heartbeat.json"
            atomic_write_json(target, {"ts": "old"})

            atomic_write_json(target, {"ts": "new"})

            self.assertEqual(json.loads(target.read_text())["ts"], "new")

    def test_creates_missing_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "history" / "v0001.yaml"

            atomic_write_text(target, "x: 1\n")

            self.assertTrue(target.exists())


class ReflectionLockTests(unittest.TestCase):
    def test_second_acquisition_is_refused_while_lock_is_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / ".reflect.lock"
            with (
                patch.object(reflect, "LOCK_PATH", lock),
                patch.object(reflect, "STATE_DIR", Path(tmp)),
            ):
                with reflect._reflection_lock():
                    self.assertTrue(lock.exists())
                    with self.assertRaises(SystemExit):
                        with reflect._reflection_lock():
                            pass
                self.assertFalse(lock.exists())

    def test_stale_lock_is_broken_and_reacquired(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / ".reflect.lock"
            lock.write_text("99999\n")
            stale = time.time() - reflect.LOCK_STALE_SECONDS - 10
            os.utime(lock, (stale, stale))

            with (
                patch.object(reflect, "LOCK_PATH", lock),
                patch.object(reflect, "STATE_DIR", Path(tmp)),
            ):
                with reflect._reflection_lock():
                    self.assertTrue(lock.exists())
            self.assertFalse(lock.exists())

    def test_lock_is_released_when_body_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / ".reflect.lock"
            with (
                patch.object(reflect, "LOCK_PATH", lock),
                patch.object(reflect, "STATE_DIR", Path(tmp)),
            ):
                with self.assertRaises(RuntimeError):
                    with reflect._reflection_lock():
                        raise RuntimeError("boom")
                self.assertFalse(lock.exists())


if __name__ == "__main__":
    unittest.main()
