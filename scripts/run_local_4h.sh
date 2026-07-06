#!/usr/bin/env bash
# Launches 0rum-trading on the validated 4h long-only AK MACD setup.
#
# Uses the self-contained Python producer (orum.external.ak_macd_producer),
# NOT the legacy TradingView/CDP bridge (orum.external.bridge) -- the producer
# fetches candles itself from Binance and needs no TradingView Desktop window
# open, which matters for an unattended overnight run. goal.yaml already
# pins timeframe=4h and allow_short=false (2026-07-01).
#
# Usage: ./scripts/run_local_4h.sh
# Stop:  Ctrl-C (kills the whole process group), or pkill -f run_local_4h.sh
set -u
cd "$(dirname "$0")/.."

supervise() {
  local name="$1"
  shift
  while true; do
    "$@"
    local status=$?
    echo "[$name] exited with status $status; restarting in 10s" >&2
    sleep 10
  done
}

supervise worker uv run python -m orum.run &
supervise watcher uv run python -m orum.orum_watch &
supervise dashboard uv run python -m orum.dashboard &
# Mode is SHADOW by default (logs verdicts, zero trades). Flip to live paper
# execution with:  AK_MACD_LIVE=1 ./scripts/run_local_4h.sh
PRODUCER_FLAGS="--interval 60"
[ -n "${AK_MACD_LIVE:-}" ] && PRODUCER_FLAGS="--live ${PRODUCER_FLAGS}"
supervise producer uv run python -m orum.external.ak_macd_producer ${PRODUCER_FLAGS} &

trap 'kill 0' INT TERM
wait
