"""Obsolete entry points fail before launching or stopping any process."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("relative", [
    "run_all.sh", "scripts/run_engine.sh", "scripts/run_local.sh",
    "scripts/run_local_4h.sh", "scripts/run_native_lab.sh",
])
def test_retired_launcher_has_no_process_operations(relative, tmp_path):
    script = ROOT / relative
    # Check the restricted stub before executing it: a regression must never
    # resurrect an old supervisor while the test suite is running.
    operations = [line for line in script.read_text().splitlines() if line and not line.startswith("#")]
    assert len(operations) == 2
    assert operations[0].startswith("printf '%s\\n' '")
    assert operations[1] == "exit 64"
    result = subprocess.run(["/bin/bash", str(script)], cwd=tmp_path,
                            env={"PATH": str(tmp_path)}, capture_output=True, text=True, timeout=2)
    assert result.returncode == 64
    assert "retired" in result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("command", ["start", "stop", "restart"])
def test_obsolete_cli_control_is_rejected_before_project_access(command, tmp_path):
    script = ROOT / "scripts/0rum"
    prefix = script.read_text().split("PROJECT=", 1)[0]
    assert "start|stop|restart)" in prefix and "exit 64" in prefix
    # Execute only the guard: even a regression cannot reach the hardcoded
    # operational directory or the dashboard controls during tests.
    guard = tmp_path / "guard.sh"
    guard.write_text(prefix + "\nexit 99\n")
    result = subprocess.run(["/bin/bash", str(guard), command], cwd=tmp_path,
                            env={"PATH": str(tmp_path)}, capture_output=True, text=True, timeout=2)
    assert result.returncode == 64
    assert "no service was changed" in result.stderr
