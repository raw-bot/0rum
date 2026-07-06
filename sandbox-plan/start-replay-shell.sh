#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/Applications/0rum"
REPLAY_HOME="$PROJECT_ROOT/.sandbox/0rum-one-shot-home"

mkdir -p "$REPLAY_HOME/0rum-trading"
mkdir -p "$REPLAY_HOME/0rum-trading-config"

export HOME="$REPLAY_HOME"
export ORUM_TRADING_MODE="paper"
export ORUM_TRADING_I_ACCEPT_RISK="false"
unset EXCHANGE_API_KEY
unset EXCHANGE_API_SECRET
unset GLASSNODE_API_KEY
unset NEWS_API_KEY

cd "$PROJECT_ROOT"

printf '0rum one-shot replay shell\n'
printf 'Project: %s\n' "$PROJECT_ROOT"
printf 'HOME=%s\n' "$HOME"
printf 'MODE=%s\n' "$ORUM_TRADING_MODE"
printf 'RISK=%s\n' "$ORUM_TRADING_I_ACCEPT_RISK"
printf '\nHard stop: do not run 0rum installer globally during first replay.\n'
printf 'Prompt: %s\n\n' "$PROJECT_ROOT/Docs/0rum Prompt.md"

exec "${SHELL:-/bin/zsh}"
