#!/usr/bin/env bash
# Launches the three hermes-trading processes with auto-restart.
#
# The worker deliberately exits after 5 consecutive failures; without a
# supervisor that meant silent death (only a stale heartbeat revealed it).
# Restart events are visible in each process's stdout and in
# state/events.jsonl (worker_boot entries).
#
# Usage: ./scripts/run_local.sh
# Stop:  Ctrl-C (kills the whole process group)

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

supervise worker uv run python -m hermes_trading.run &
supervise watcher uv run python -m hermes_trading.hermes_watch &
supervise dashboard uv run python -m hermes_trading.dashboard &

trap 'kill 0' INT TERM
wait
