"""Redacted access to generic passwords in the macOS Keychain."""

from __future__ import annotations

import getpass
import subprocess
from collections.abc import Callable


class KeychainSecretError(RuntimeError):
    """Raised without provider output when a credential cannot be retrieved."""


def read_generic_password(
    *,
    service: str = "0rum-openrouter",
    account: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """Return one Keychain secret without exposing command stderr on failure."""

    result = runner(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            account or getpass.getuser(),
            "-s",
            service,
            "-w",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    secret = result.stdout.strip() if result.returncode == 0 else ""
    if not secret:
        raise KeychainSecretError(
            "OpenRouter credential unavailable in macOS Keychain"
        )
    return secret
