#!/usr/bin/env bash
# Always-on dashboard launcher (used by the launchd agent com.hermes.dashboard).
# Runs through `uv run` so the project's venv is resolved exactly as it is in a
# terminal — launching .venv/bin/python3 directly from launchd hangs in
# interpreter path init on this machine.
set -u
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export PYTHONUNBUFFERED=1
exec uv run python -m hermes_trading.dashboard
