# LLM Paper Runtime Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the existing DeepSeek V4 Pro laboratory hourly as an isolated, auditable, switchable paper-only LaunchAgent whose OpenRouter credential comes from macOS Keychain.

**Architecture:** A testable Keychain adapter supplies the credential to a one-shot paper runner, which invokes the validated `paper_autonomous` entrypoint and atomically publishes a redacted heartbeat. A dedicated `com.0rum.llm-paper` LaunchAgent schedules it every 3600 seconds, independently of both the retired legacy engine and the authoritative `com.0rum.paper` portfolio agent.

**Tech Stack:** Python 3.11+, macOS `/usr/bin/security`, launchd property lists, existing `orum.fsio` atomic writes, pytest, vanilla dashboard JavaScript, OpenRouter strict JSON responses.

---

## File map

- Create `orum/llm/keychain.py`: retrieve one generic-password item without logging its value.
- Create `orum/llm/paper_agent.py`: run exactly one autonomous paper cycle and publish redacted status.
- Create `scripts/run_llm_paper_agent.py`: thin executable wrapper.
- Create `scripts/install_llm_paper_agent.sh`: install, enable, disable, status, and uninstall the non-secret LaunchAgent.
- Modify `orum/paths.py`: add the redirectable runtime status path.
- Modify `orum/dashboard.py`: expose a bounded runtime status object.
- Modify `orum/static/dashboard.js`: render agent/model/cadence/result state read-only.
- Modify `docs/llm-trading-lab.md`: document Keychain and agent operations.
- Create `tests/test_llm_keychain.py`, `tests/test_llm_paper_agent.py`, and `tests/test_llm_launch_agent.py`.
- Modify `tests/test_dashboard_llm.py`.
- Install operational file `~/Library/LaunchAgents/com.0rum.llm-paper.plist` only after the canary passes.

### Task 1: Keychain boundary

**Files:**
- Create: `orum/llm/keychain.py`
- Create: `tests/test_llm_keychain.py`

- [ ] **Step 1: Write the failing retrieval and redaction tests**

```python
from subprocess import CompletedProcess

import pytest

from orum.llm.keychain import KeychainSecretError, read_generic_password


def test_read_generic_password_uses_security_without_secret_in_arguments():
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return CompletedProcess(args, 0, stdout="secret-from-keychain\n", stderr="")

    value = read_generic_password(service="0rum-openrouter", account="cube", runner=runner)

    assert value == "secret-from-keychain"
    assert calls[0][0] == [
        "/usr/bin/security", "find-generic-password",
        "-a", "cube", "-s", "0rum-openrouter", "-w",
    ]
    assert "secret-from-keychain" not in repr(calls)


@pytest.mark.parametrize("returncode,stdout", [(44, ""), (0, "\n")])
def test_failure_never_exposes_security_stderr(returncode, stdout):
    def runner(args, **kwargs):
        return CompletedProcess(args, returncode, stdout=stdout, stderr="sensitive-output")

    with pytest.raises(KeychainSecretError, match="OpenRouter credential unavailable") as exc:
        read_generic_password(service="0rum-openrouter", account="cube", runner=runner)

    assert "sensitive-output" not in str(exc.value)
```

- [ ] **Step 2: Verify the tests fail before implementation**

Run: `.venv/bin/python -m pytest -q tests/test_llm_keychain.py`

Expected: collection fails because `orum.llm.keychain` is absent.

- [ ] **Step 3: Implement the minimal adapter**

```python
"""Redacted access to generic passwords in the macOS Keychain."""

import getpass
import subprocess
from collections.abc import Callable


class KeychainSecretError(RuntimeError):
    pass


def read_generic_password(
    *, service: str = "0rum-openrouter", account: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    result = runner(
        ["/usr/bin/security", "find-generic-password", "-a", account or getpass.getuser(),
         "-s", service, "-w"],
        capture_output=True, text=True, check=False,
    )
    secret = result.stdout.strip() if result.returncode == 0 else ""
    if not secret:
        raise KeychainSecretError("OpenRouter credential unavailable in macOS Keychain")
    return secret
```

- [ ] **Step 4: Run focused tests and commit**

Run: `.venv/bin/python -m pytest -q tests/test_llm_keychain.py`

Expected: pass without secret-like output.

```bash
git add orum/llm/keychain.py tests/test_llm_keychain.py
git commit -m "feat: read OpenRouter credential from Keychain"
```

### Task 2: One-shot paper agent and heartbeat

**Files:**
- Create: `orum/llm/paper_agent.py`
- Create: `scripts/run_llm_paper_agent.py`
- Modify: `orum/paths.py`
- Create: `tests/test_llm_paper_agent.py`

- [ ] **Step 1: Add the state path**

```python
LLM_RUNTIME_STATUS_PATH = STATE_DIR / "llm_runtime_status.json"
```

- [ ] **Step 2: Write failing cycle tests**

```python
import json
from datetime import UTC, datetime

from orum.llm.paper_agent import run_paper_cycle


def test_cycle_uses_memory_only_secret_and_publishes_redacted_success(tmp_path):
    seen = {}

    def run_once(argv, *, environ):
        seen["argv"] = argv
        seen["key"] = environ["OPENROUTER_API_KEY"]
        return 0

    code = run_paper_cycle(
        status_path=tmp_path / "status.json",
        key_loader=lambda: "memory-secret",
        run_once=run_once,
        now=lambda: datetime(2026, 7, 13, 20, 0, tzinfo=UTC),
    )

    assert code == 0
    assert seen["argv"] == ["--mode", "paper_autonomous", "--once", "--confirm-paper"]
    status_text = (tmp_path / "status.json").read_text()
    assert json.loads(status_text)["last_result"] == "ok"
    assert "memory-secret" not in status_text


def test_keychain_failure_is_redacted_and_model_is_not_called(tmp_path):
    called = False

    def run_once(argv, *, environ):
        nonlocal called
        called = True
        return 0

    code = run_paper_cycle(
        status_path=tmp_path / "status.json",
        key_loader=lambda: (_ for _ in ()).throw(RuntimeError("sensitive")),
        run_once=run_once,
    )

    status_text = (tmp_path / "status.json").read_text()
    assert code == 2
    assert called is False
    assert json.loads(status_text)["last_error"] == "OpenRouter credential unavailable"
    assert "sensitive" not in status_text
```

- [ ] **Step 3: Confirm failure, then implement the one-shot agent**

Run: `.venv/bin/python -m pytest -q tests/test_llm_paper_agent.py`

Expected: collection fails because `orum.llm.paper_agent` is absent.

The implementation imports `scripts.run_llm_lab.main` lazily and invokes it
with exactly:

```python
PAPER_ARGS = ["--mode", "paper_autonomous", "--once", "--confirm-paper"]
```

It writes only `enabled`, `running`, `model`, `interval_minutes`,
`last_cycle_started_at`, `last_cycle_completed_at`, `last_result`, and
`last_error` through `atomic_write_json`. It never serializes the environment,
the key loader exception, or OpenRouter headers. A disabled invocation exits
zero before Keychain access.

- [ ] **Step 4: Add the thin wrapper and pass tests**

```python
#!/usr/bin/env python3
from orum.llm.paper_agent import main


if __name__ == "__main__":
    raise SystemExit(main())
```

Run: `.venv/bin/python -m pytest -q tests/test_llm_keychain.py tests/test_llm_paper_agent.py`

Run: `.venv/bin/python -m compileall -q orum scripts`

Expected: both commands exit zero.

- [ ] **Step 5: Commit the agent**

```bash
git add orum/paths.py orum/llm/paper_agent.py scripts/run_llm_paper_agent.py tests/test_llm_paper_agent.py
git commit -m "feat: add one-shot LLM paper agent"
```

### Task 3: Dedicated LaunchAgent operations

**Files:**
- Create: `scripts/install_llm_paper_agent.sh`
- Create: `tests/test_llm_launch_agent.py`

- [ ] **Step 1: Write failing installation-contract tests**

```python
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_installer_is_valid_bash():
    result = subprocess.run(["/bin/bash", "-n", str(ROOT / "scripts/install_llm_paper_agent.sh")])
    assert result.returncode == 0


def test_installed_template_is_hourly_paper_only_and_secret_free():
    body = (ROOT / "scripts/install_llm_paper_agent.sh").read_text()
    assert "com.0rum.llm-paper" in body
    assert "run_llm_paper_agent.py" in body
    assert "<integer>3600</integer>" in body
    assert "LLM_PAPER_ENABLED" in body
    assert "OPENROUTER_API_KEY" not in body
    assert "KeepAlive" not in body
```

- [ ] **Step 2: Confirm failure, then implement explicit operations**

Run: `.venv/bin/python -m pytest -q tests/test_llm_launch_agent.py`

Expected: failure because the installer is absent.

The script accepts only `install`, `enable`, `disable`, `status`, and `uninstall`.
`install` writes `~/Library/LaunchAgents/com.0rum.llm-paper.plist` with
`RunAtLoad`, `StartInterval=3600`, `LLM_PAPER_ENABLED=1`, the project PATH/HOME,
and separate redacted stdout/stderr files. It runs `plutil -lint` before
bootstrap. `disable` boots out the agent but preserves journals. `uninstall`
boots it out and removes only the plist.

- [ ] **Step 3: Run tests and commit**

Run: `/bin/bash -n scripts/install_llm_paper_agent.sh`

Run: `.venv/bin/python -m pytest -q tests/test_llm_launch_agent.py`

Expected: both pass.

```bash
git add scripts/install_llm_paper_agent.sh tests/test_llm_launch_agent.py
git commit -m "feat: install hourly LLM paper LaunchAgent"
```

### Task 4: Dashboard runtime audit

**Files:**
- Modify: `orum/dashboard.py`
- Modify: `orum/static/dashboard.js`
- Modify: `tests/test_dashboard_llm.py`

- [ ] **Step 1: Write the failing bounded-status test**

```python
def test_llm_lab_exposes_only_bounded_runtime_status(tmp_path):
    (tmp_path / "llm_runtime_status.json").write_text(json.dumps({
        "enabled": True, "running": False,
        "model": "deepseek/deepseek-v4-pro", "interval_minutes": 60,
        "last_cycle_started_at": NOW.isoformat(),
        "last_cycle_completed_at": NOW.isoformat(),
        "last_result": "ok", "last_error": "", "unexpected": "hidden",
    }))
    runtime = dashboard._llm_lab_state(tmp_path, now=NOW)["runtime"]
    assert runtime["model"] == "deepseek/deepseek-v4-pro"
    assert runtime["last_result"] == "ok"
    assert "unexpected" not in runtime
```

- [ ] **Step 2: Extend static tests**

Assert that `renderLlmLab` reads `lab.runtime`, renders `runtime.model`,
`runtime.last_result`, and the latest timestamps, and escapes
`runtime.last_error`.

- [ ] **Step 3: Confirm failure, implement normalization, and render**

Run: `.venv/bin/python -m pytest -q tests/test_dashboard_llm.py`

Expected: new runtime assertions fail.

`_llm_lab_state` reads `llm_runtime_status.json`, copies only the eight public
fields, and supplies a safe absent default. The dashboard renders one read-only
runtime strip and no activation button or credential endpoint.

- [ ] **Step 4: Verify and commit dashboard changes**

Run: `.venv/bin/python -m pytest -q tests/test_dashboard_llm.py tests/test_dashboard_state.py`

Run: `node --check orum/static/dashboard.js`

Expected: pass.

```bash
git add orum/dashboard.py orum/static/dashboard.js tests/test_dashboard_llm.py
git commit -m "feat: expose redacted LLM agent status"
```

### Task 5: Documentation and full regression suite

**Files:**
- Modify: `docs/llm-trading-lab.md`

- [ ] **Step 1: Document secure setup and agent operations**

Document the prompted Keychain insertion command and:

```bash
./scripts/install_llm_paper_agent.sh install
./scripts/install_llm_paper_agent.sh disable
./scripts/install_llm_paper_agent.sh enable
./scripts/install_llm_paper_agent.sh status
./scripts/install_llm_paper_agent.sh uninstall
```

State that disable/uninstall never removes journals or Keychain data, and that
the key must never appear on a command line.

- [ ] **Step 2: Run verification**

Run: `rg -n "sk-or-|Bearer sk-" orum scripts docs tests`

Expected: no real credential.

Run: `git diff --check`

Run: `.venv/bin/python -m pytest -q`

Run: `.venv/bin/python -m compileall -q orum scripts`

Run: `node --check orum/static/dashboard.js`

Expected: all commands exit zero.

- [ ] **Step 3: Commit documentation**

```bash
git add docs/llm-trading-lab.md
git commit -m "docs: document hourly LLM paper agent"
```

### Task 6: Canary, activation, and operational proof

**Files:**
- Install: `~/Library/LaunchAgents/com.0rum.llm-paper.plist`
- Runtime only: `state/llm_runtime_status.json` and existing `state/llm_*.jsonl`

- [ ] **Step 1: Verify Keychain presence without reading it**

Run: `security find-generic-password -a "$USER" -s "0rum-openrouter" >/dev/null`

Expected: exit zero and no stdout.

- [ ] **Step 2: Run one internal canary cycle**

Invoke `orum.llm.paper_agent.run_paper_cycle()` so Keychain access happens
inside Python. Before and after, hash native `paper_positions.json` and
`paper_fills.jsonl`. Print only exit code, model slug, decision IDs, validation
status, request ID, latency, usage, and timestamps.

Expected: requested and returned model are exactly
`deepseek/deepseek-v4-pro`; both LLM lanes produce auditable paper records; the
native file hashes are unchanged; no credential appears in output or state.

- [ ] **Step 3: Install and enable the hourly agent**

Run: `./scripts/install_llm_paper_agent.sh install`

Expected: plist passes `plutil`, contains no credential, and
`launchctl print gui/$(id -u)/com.0rum.llm-paper` succeeds.

- [ ] **Step 4: Restart the dashboard under the existing authorization**

Run: `launchctl kickstart -k "gui/$(id -u)/com.0rum.dashboard"`

Expected: dashboard answers on `127.0.0.1:8787`. Do not start the retired legacy
engine. Confirm `legacy_audit.process_running` remains false and
`com.0rum.paper` remains healthy.

- [ ] **Step 5: Verify final runtime state**

Inspect agent status, redacted log tails, and `/api/state`, printing only LLM
enabled/running/model/cadence/result/timestamps and reference/evolving decision
IDs. Confirm no API object contains an authorization header, environment, or
Keychain value.

- [ ] **Step 6: Final scope and secret audit**

Run GitNexus change detection for implementation files, `git diff --check`, the
full test suite, JavaScript syntax check, and the secret-pattern scan. Preserve
all unrelated dirty worktree changes.
