#!/usr/bin/env bash
# 0rum — AK MACD SHADOW lab.
#
#   dashboard + AK MACD bridge (SHADOW)
#
# Observation ONLY. The bridge reads TradingView and logs candidate verdicts; it
# NEVER opens a position, NEVER writes trades.jsonl or open_position.json. There
# is NO worker and NO watcher here — the native RSI lab is a separate launcher
# (scripts/run_native_lab.sh). Use this while qualifying AK MACD signals.
#
# Refuses to start if a dashboard or a bridge is already running (one owner).
# Ctrl-C stops the bridge and the dashboard cleanly.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${ORUM_DASHBOARD_HOST:-127.0.0.1}"
PORT="${ORUM_DASHBOARD_PORT:-8787}"
BASE="http://${HOST}:${PORT}"
INTERVAL="${AK_SHADOW_INTERVAL:-120}"
DASH_LOG="state/ak_shadow_dashboard.log"
BRIDGE_LOG="state/ak_shadow_bridge.log"

# --- one owner: refuse duplicates -------------------------------------------
if curl -s -o /dev/null "${BASE}/api/state"; then
  echo "✗ a dashboard already answers on ${BASE}."
  echo "  Stop it first:  pkill -f orum.dashboard"
  exit 1
fi
if pgrep -f "orum.external.bridge" >/dev/null; then
  echo "✗ an AK MACD bridge is already running."
  echo "  Stop it first:  pkill -f orum.external.bridge"
  exit 1
fi

echo "▶ AK MACD SHADOW lab — dashboard + bridge(shadow). No worker, no watcher, no trades."
uv run python -m orum.dashboard >>"${DASH_LOG}" 2>&1 &
DASH_PID=$!

cleanup() {
  trap - INT TERM EXIT
  echo ""
  echo "■ stopping AK shadow bridge …"
  pkill -f "orum.external.bridge" 2>/dev/null || true
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

echo "▶ starting AK MACD bridge (shadow, interval ${INTERVAL}s)"
uv run python -m orum.external.bridge --shadow --interval "${INTERVAL}" >>"${BRIDGE_LOG}" 2>&1 &

echo "────────────────────────────────────────────"
echo "  Mode      : AK MACD SHADOW (observation only)"
echo "  Dashboard : ${BASE}   (log: ${DASH_LOG})"
echo "  Bridge    : shadow, ${INTERVAL}s"
echo "              verdicts -> state/ak_macd_shadow.jsonl   (log: ${BRIDGE_LOG})"
echo "  Worker    : OFF        Watcher : OFF"
echo "  Stop all  : Ctrl-C here"
echo "────────────────────────────────────────────"

wait "${DASH_PID}"
