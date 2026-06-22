#!/usr/bin/env bash
# Hermes — single launcher for the dashboard + trading worker.
#
# Starts the dashboard server, waits until it answers, then starts the worker
# THROUGH the dashboard's own endpoint so the ON/OFF toggle, the pid file
# (state/worker.pid) and this script all agree on one source of truth.
# Ctrl-C stops the worker and the dashboard (and uv's child python) cleanly.
set -euo pipefail

cd "$(dirname "$0")"

HOST="${HERMES_DASHBOARD_HOST:-127.0.0.1}"
PORT="${HERMES_DASHBOARD_PORT:-8787}"
BASE="http://${HOST}:${PORT}"

# Refuse to start a second dashboard on the same port (the classic cause of
# "my clicks do nothing": an old server with stale code answers instead).
if curl -s -o /dev/null "${BASE}/api/state"; then
  echo "✗ something already answers on ${BASE}."
  echo "  Stop it first:  pkill -f hermes_trading.dashboard"
  exit 1
fi

echo "▶ starting Hermes dashboard on ${BASE} …"
uv run python -m hermes_trading.dashboard &
DASH_PID=$!

cleanup() {
  trap - INT TERM EXIT
  echo ""
  echo "■ stopping worker …"
  curl -s -X POST "${BASE}/api/worker/stop" >/dev/null 2>&1 || true
  echo "■ stopping dashboard …"
  pkill -P "${DASH_PID}" 2>/dev/null || true   # uv run spawns a child python
  kill "${DASH_PID}" 2>/dev/null || true
  wait "${DASH_PID}" 2>/dev/null || true
  echo "✓ stopped."
}
trap cleanup INT TERM EXIT

echo "… waiting for dashboard to come up"
for _ in $(seq 1 40); do
  if curl -s -o /dev/null "${BASE}/api/state"; then break; fi
  sleep 0.5
done

echo "▶ starting worker …"
curl -s -X POST "${BASE}/api/worker/start" >/dev/null || true
echo ""
echo "────────────────────────────────────────────"
echo "  Dashboard : ${BASE}"
echo "  Worker    : ON/OFF toggle in the top bar"
echo "  Stop all  : Ctrl-C here"
echo "────────────────────────────────────────────"

wait "${DASH_PID}"
