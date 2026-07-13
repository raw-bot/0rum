#!/usr/bin/env bash
# 0rum TRADING ENGINE — worker + watcher + LOCAL AK MACD producer, auto-restart.
#
# This is the part that actually trades. The dashboard is NOT here: it runs
# separately (always-on, via launchd) and starts/stops THIS script as a single
# detached session. Because every process below shares one process group, the
# dashboard / `0rum` CLI can stop the whole engine with one killpg — without
# ever touching the dashboard itself.
#
# Signal brain = orum.external.ak_macd_producer (LOCAL Python, Binance klines).
# 2026-07-06: replaced the TradingView CDP bridge — the bot computes its own
# signals; TradingView is a visual/audit mirror only and does NOT need to run.
# (The old bridge remains available: orum.external.bridge, shadow-first.)
#
# Mode is SHADOW by default (logs verdicts, zero trades). Live paper execution:
#   AK_MACD_LIVE=1 ./scripts/run_engine.sh
#
# Usage:  ./scripts/run_engine.sh   (normally launched by the dashboard, `0rum`,
#         or the launchd agent com.0rum.engine — which sets AK_MACD_LIVE=1)
# Stop:   SIGTERM/SIGINT to the process group (Ctrl-C, killpg, or `0rum stop`).
#         Under launchd: launchctl unload ~/Library/LaunchAgents/com.0rum.engine.plist

set -u
cd "$(dirname "$0")/.."

# launchd starts with a bare PATH: make uv resolvable exactly like run_dash.sh.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export PYTHONUNBUFFERED=1

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

PRODUCER_FLAGS="--interval 60"
[ -n "${AK_MACD_LIVE:-}" ] && PRODUCER_FLAGS="--live ${PRODUCER_FLAGS}"
supervise producer uv run python -m orum.external.ak_macd_producer ${PRODUCER_FLAGS} &

wait
