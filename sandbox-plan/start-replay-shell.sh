#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/Users/cube/Documents/00-code/HermesTrading"
REPLAY_HOME="$PROJECT_ROOT/.sandbox/hermes-one-shot-home"

mkdir -p "$REPLAY_HOME/hermes-trading"
mkdir -p "$REPLAY_HOME/hermes-trading-config"

export HOME="$REPLAY_HOME"
export HERMES_TRADING_MODE="paper"
export HERMES_TRADING_I_ACCEPT_RISK="false"
unset EXCHANGE_API_KEY
unset EXCHANGE_API_SECRET
unset GLASSNODE_API_KEY
unset NEWS_API_KEY

cd "$PROJECT_ROOT"

printf 'Hermes one-shot replay shell\n'
printf 'Project: %s\n' "$PROJECT_ROOT"
printf 'HOME=%s\n' "$HOME"
printf 'MODE=%s\n' "$HERMES_TRADING_MODE"
printf 'RISK=%s\n' "$HERMES_TRADING_I_ACCEPT_RISK"
printf '\nHard stop: do not run Hermes installer globally during first replay.\n'
printf 'Prompt: %s\n\n' "$PROJECT_ROOT/Docs/Hermes Prompt.md"

exec "${SHELL:-/bin/zsh}"
