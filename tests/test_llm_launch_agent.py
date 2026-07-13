import plistlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install_llm_paper_agent.sh"
TEMPLATE = ROOT / "config/com.0rum.llm-paper.plist"


def test_installer_is_valid_bash():
    result = subprocess.run(
        ["/bin/bash", "-n", str(INSTALLER)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    usage = subprocess.run(
        ["/bin/bash", str(INSTALLER), "unsupported-operation"],
        capture_output=True,
        text=True,
    )
    assert usage.returncode == 2
    assert "command not found" not in usage.stderr
    assert "usage:" in usage.stderr


def test_plist_is_hourly_paper_only_and_secret_free():
    with TEMPLATE.open("rb") as handle:
        config = plistlib.load(handle)

    assert config["Label"] == "com.0rum.llm-paper"
    assert config["StartInterval"] == 3600
    assert config["RunAtLoad"] is True
    assert "KeepAlive" not in config
    assert config["EnvironmentVariables"]["LLM_PAPER_ENABLED"] == "1"
    assert any(
        "run_llm_paper_agent.py" in argument
        for argument in config["ProgramArguments"]
    )
    body = TEMPLATE.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY" not in body
    assert "sk-or-" not in body


def test_installer_exposes_explicit_lifecycle_operations_only():
    body = INSTALLER.read_text(encoding="utf-8")

    for operation in ("install", "enable", "disable", "status", "uninstall"):
        assert f"{operation})" in body
    assert "com.0rum.llm-paper" in body
    assert "config/com.0rum.llm-paper.plist" in body
    assert "OPENROUTER_API_KEY" not in body
