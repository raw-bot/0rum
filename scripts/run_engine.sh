#!/usr/bin/env bash
# 0rum TRADING ENGINE — worker + watcher + AK MACD bridge, with auto-restart.
#
# This is the part that actually trades. The dashboard is NOT here: it runs
# separately (always-on, via launchd) and starts/stops THIS script as a single
# detached session. Because every process below shares one process group, the
# dashboard / `0rum` CLI can stop the whole engine with one killpg — without
# ever touching the dashboard itself.
#
# Signal brain = the "AK MACD 15m" Pine study on TradingView Desktop, polled by
# the bridge over CDP. If TradingView is closed or the study isn't on the chart,
# the engine runs but receives no signals.
#
# Mode is SHADOW by default (logs verdicts, zero trades). Live paper execution:
#   AK_MACD_LIVE=1 ./scripts/run_engine.sh
#
# Usage:  ./scripts/run_engine.sh   (normally launched by the dashboard or `0rum`)
# Stop:   SIGTERM/SIGINT to the process group (Ctrl-C, killpg, or `0rum stop`)

set -u
cd "$(dirname "$0")/.."

# On any term/int: signal the whole group then exit immediately. `exit 0` keeps
# the supervise loops from "restarting" a child that was killed on purpose.
trap 'kill 0 2>/dev/null; exit 0' INT TERM

supervise() {
  local name="$1"
  shift
  while true; do
    "$@"
    local status=$?
    echo "[engine:$name] exited with status $status; restarting in 10s" >&2
    sleep 10
  done
}

supervise worker  uv run python -m orum.run &
supervise watcher uv run python -m orum.orum_watch &

BRIDGE_FLAGS="--interval 30"
[ -n "${AK_MACD_LIVE:-}" ] && BRIDGE_FLAGS="--live ${BRIDGE_FLAGS}"
supervise bridge  uv run python -m orum.external.bridge ${BRIDGE_FLAGS} &

wait
