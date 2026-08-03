"""One isolated, launchd-scheduled LLM paper cycle."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from orum.fsio import atomic_write_json
from orum.llm.keychain import read_generic_password
from orum.paths import LLM_RUNTIME_STATUS_PATH


MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
INTERVAL_MINUTES = 60
PAPER_ARGS = [
    "--mode", "paper_autonomous", "--once", "--confirm-paper",
    "--provider", "nvidia", "--model", MODEL,
    "--request-timeout-seconds", "300",
]


def _default_run_once(argv: list[str], *, environ: Mapping[str, str]) -> int:
    from scripts.run_llm_lab import main as run_llm_lab

    return run_llm_lab(argv, environ=environ)


def _status(
    *,
    running: bool,
    started_at: str,
    completed_at: str | None,
    result: str,
    error: str,
) -> dict:
    return {
        "enabled": True,
        "running": running,
        "model": MODEL,
        "interval_minutes": INTERVAL_MINUTES,
        "last_cycle_started_at": started_at,
        "last_cycle_completed_at": completed_at,
        "last_result": result,
        "last_error": error,
    }


def run_paper_cycle(
    *,
    status_path: Path = LLM_RUNTIME_STATUS_PATH,
    key_loader: Callable[[], str] = lambda: read_generic_password(service="0rum-nvidia"),
    run_once: Callable[..., int] = _default_run_once,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Run one paper-only cycle while keeping the Keychain value in memory."""

    started_at = now().astimezone(UTC).isoformat()
    atomic_write_json(
        status_path,
        _status(
            running=True,
            started_at=started_at,
            completed_at=None,
            result="running",
            error="",
        ),
    )
    try:
        api_key = key_loader()
    except Exception:
        completed_at = now().astimezone(UTC).isoformat()
        atomic_write_json(
            status_path,
            _status(
                running=False,
                started_at=started_at,
                completed_at=completed_at,
                result="configuration_error",
                error="NVIDIA credential unavailable",
            ),
        )
        return 2

    environment = {"NVIDIA_API_KEY": api_key}
    try:
        exit_code = run_once(list(PAPER_ARGS), environ=environment)
    except Exception:
        exit_code = 1
    completed_at = now().astimezone(UTC).isoformat()
    result = "ok" if exit_code == 0 else "cycle_error"
    error = "" if exit_code == 0 else f"LLM paper cycle exited with status {exit_code}"
    atomic_write_json(
        status_path,
        _status(
            running=False,
            started_at=started_at,
            completed_at=completed_at,
            result=result,
            error=error,
        ),
    )
    return exit_code


def main(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    status_path: Path = LLM_RUNTIME_STATUS_PATH,
    key_loader: Callable[[], str] = lambda: read_generic_password(service="0rum-nvidia"),
    run_once: Callable[..., int] = _default_run_once,
) -> int:
    del argv
    environment = os.environ if environ is None else environ
    if environment.get("LLM_PAPER_ENABLED") != "1":
        return 0
    return run_paper_cycle(
        status_path=status_path,
        key_loader=key_loader,
        run_once=run_once,
    )
