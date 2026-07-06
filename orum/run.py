from __future__ import annotations

import argparse
import asyncio
import atexit
import fcntl
import os
import signal
import sys

import yaml

from orum.loop import run_loop
from orum.paths import GOAL_PATH, STATE_DIR

# Single source of truth for "is a worker running?". The worker itself owns
# state/worker.pid (not just the dashboard), so a worker started from any path
# — run_local.sh, a bare terminal, or the dashboard toggle — is visible to the
# dashboard and guarded against duplicates. The flock is the hard guarantee:
# the OS releases it automatically when the process dies, so it never goes stale.
WORKER_LOCK_PATH = STATE_DIR / "worker.lock"
WORKER_PID_PATH = STATE_DIR / "worker.pid"


def _acquire_single_instance_lock():
    """Refuse to start if another worker already holds the lock.

    Returns the open lock file object; the caller must keep a reference to it so
    the lock is held for the process's lifetime.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_fd = open(WORKER_LOCK_PATH, "w")  # noqa: SIM115 - held for process lifetime
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            existing = WORKER_PID_PATH.read_text().strip()
        except OSError:
            existing = "?"
        sys.stderr.write(
            "0rum worker: another worker is already running "
            f"(pid {existing or '?'}); refusing to start a second one.\n"
        )
        lock_fd.close()
        raise SystemExit(3)

    WORKER_PID_PATH.write_text(str(os.getpid()))

    def _release() -> None:
        WORKER_PID_PATH.unlink(missing_ok=True)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
        except OSError:
            pass

    atexit.register(_release)
    # atexit does not fire on SIGTERM (how run_local.sh / the dashboard stop us),
    # so translate the common termination signals into a clean exit that does.
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: sys.exit(0))
    return lock_fd


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", help="Override the asset configured in state/goal.yaml")
    args = parser.parse_args()

    _lock = _acquire_single_instance_lock()  # noqa: F841 - keeps the flock held

    goal = yaml.safe_load(GOAL_PATH.read_text()) or {}
    if args.asset:
        goal["asset"] = args.asset
    asyncio.run(run_loop(goal))


if __name__ == "__main__":
    main()
