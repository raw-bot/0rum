"""The inspection command must describe the exact config it constructed."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_paper_portfolio as runner


@pytest.mark.parametrize("override_exists", [False, True])
def test_dry_reports_the_selected_complete_config_once(tmp_path, monkeypatch, capsys, override_exists):
    fallback = tmp_path / "default.yaml"
    override = tmp_path / "operator.yaml"
    fallback.write_text("starting_balance_usd: 10000\nstrategies: []\n")
    if override_exists:
        override.write_text("starting_balance_usd: 12000\nstrategies: []\n")
    source = override if override_exists else fallback
    expected = {"starting_balance_usd": 12000 if override_exists else 10000, "strategies": []}
    reads = []
    original_read = Path.read_text

    def read(path, *args, **kwargs):
        if path in (fallback, override):
            reads.append(path)
        return original_read(path, *args, **kwargs)

    built = []

    def engine(config, **kwargs):
        built.append(config)
        return SimpleNamespace(_strategies=[])

    monkeypatch.setattr(runner, "DEFAULT_PORTFOLIO_PATH", fallback)
    monkeypatch.setattr(runner, "PORTFOLIO_PATH", override)
    monkeypatch.setattr(runner, "PaperEngine", engine)
    monkeypatch.setattr(runner.sys, "argv", ["run_paper_portfolio.py", "--dry"])
    monkeypatch.setattr(Path, "read_text", read)

    assert runner.main() == 0
    output = capsys.readouterr().out
    assert f"portfolio.yaml: {source}" in output
    assert reads == [source]
    assert built == [expected]
    assert hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest() in output
