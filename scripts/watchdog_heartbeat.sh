#!/bin/bash
# 0rum WATCHDOG — pure bash on purpose: it must keep working even if uv/python
# breaks. Checks the vital signs every 5 min (launchd com.0rum.watchdog):
#   1. paper_equity.jsonl fresher than 45 min (paper portfolio runs every 15 min)
#   2. com.0rum.paper launchd job last exit == 0
#   3. > 2 GB free disk
# (Pre-2026-07-08 it watched the retired mono-asset worker + AK producer.)
# On ANY failure: macOS notification + append state/watchdog_alerts.jsonl.
# Always writes state/watchdog_status.json (so the watchdog itself is checkable).
set -u
ROOT="/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading"
STATE="$ROOT/state"
NOW=$(date +%s)
FAILS=()

age_of() { # seconds since mtime, or huge if missing
  if [ -f "$1" ]; then echo $(( NOW - $(stat -f %m "$1") )); else echo 9999999; fi
}

PAPER_AGE=$(age_of "$STATE/paper_equity.jsonl")
[ "$PAPER_AGE" -gt 2700 ] && FAILS+=("paper_equity.jsonl vieux de ${PAPER_AGE}s (>2700)")

PAPER_STATUS=$(launchctl list 2>/dev/null | awk '$3=="com.0rum.paper"{print $2}')
if [ -n "${PAPER_STATUS:-}" ] && [ "$PAPER_STATUS" != "0" ] && [ "$PAPER_STATUS" != "-" ]; then
  FAILS+=("paper agent dernier exit=$PAPER_STATUS")
fi

FREE_GB=$(df -g / | awk 'NR==2{print $4}')
[ "${FREE_GB:-0}" -lt 2 ] && FAILS+=("disque: ${FREE_GB}G libres (<2G)")

TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if [ ${#FAILS[@]} -eq 0 ]; then
  printf '{"ts":"%s","status":"ok","paper_equity_age_s":%s}\n' "$TS" "$PAPER_AGE" > "$STATE/watchdog_status.json"
else
  MSG=$(printf '%s; ' "${FAILS[@]}")
  printf '{"ts":"%s","status":"ALERT","fails":"%s"}\n' "$TS" "$MSG" > "$STATE/watchdog_status.json"
  printf '{"ts":"%s","fails":"%s"}\n' "$TS" "$MSG" >> "$STATE/watchdog_alerts.jsonl"
  # Anti-spam: notify at most once per 30 min (marker file mtime).
  MARK="$STATE/.watchdog_last_notify"
  LAST=$(age_of "$MARK")
  if [ "$LAST" -gt 1800 ]; then
    /usr/bin/osascript -e "display notification \"$MSG\" with title \"0rum WATCHDOG\" sound name \"Basso\"" 2>/dev/null
    touch "$MARK"
  fi
fi
exit 0
