#!/usr/bin/env bash
# 0rum — NATIVE RSI lab.
#
#   dashboard + worker + watcher
#
# The autonomous native (RSI DSL) paper experiment, with the 0rum reflection
# watcher. NO AK MACD bridge here. This is the "old lab" — run it only when you
# want the native strategy trading paper again. While qualifying AK MACD, use
# scripts/run_ak_shadow.sh instead and leave this OFF.
#
# Refuses to start if a dashboard is already running. Ctrl-C stops worker,
# watcher and dashboard cleanly.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${ORUM_DASHBOARD_HOST:-127.0.0.1}"
PORT="${ORUM_DASHBOARD_PORT:-8787}"
BASE="http://${HOST}:${PORT}"
DASH_LOG="state/native_dashboard.log"
WATCH_LOG="state/native_watcher.log"

if curl -s -o /dev/null "${BASE}/api/state"; then
  echo "✗ a dashboard already answers on ${BASE}."
  echo "  Stop it first:  pkill -f orum.dashboard"
  exit 1
fi

echo "▶ NATIVE RSI lab — dashboard + worker + watcher."
uv run python -m orum.dashboard >>"${DASH_LOG}" 2>&1 &
DASH_PID=$!

cleanup() {
  trap - INT TERM EXIT
  echo ""
  echo "■ stopping worker …"
  curl -s -X POST "${BASE}/api/worker/stop" >/dev/null 2>&1 || true
  echo "■ stopping watcher …"
  pkill -f "orum.orum_watch" 2>/dev/null || true
  echo "■ stopping dashboard …"
  pkill -P "${DASH_PID}" 2>/dev/null || true
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

echo "▶ starting worker (native RSI paper)"
curl -s -X POST "${BASE}/api/worker/start" >/dev/null || true

echo "▶ starting 0rum watcher (reflection)"
uv run python -m orum.orum_watch >>"${WATCH_LOG}" 2>&1 &

echo "────────────────────────────────────────────"
echo "  Mode      : NATIVE RSI (autonomous paper)"
echo "  Dashboard : ${BASE}   (log: ${DASH_LOG})"
echo "  Worker    : ON         Watcher : ON   (log: ${WATCH_LOG})"
echo "  Bridge    : OFF"
echo "  Stop all  : Ctrl-C here"
echo "────────────────────────────────────────────"

wait "${DASH_PID}"
