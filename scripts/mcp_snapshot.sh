#!/bin/bash
# Full read-only snapshot of 0rum via orum-mcp: calls every MCP tool once with
# maximal limits and writes ONE JSON object to stdout (or $1 if given) —
# ready to paste into any AI. Secrets are masked by the server itself.
#
# Usage:  ./scripts/mcp_snapshot.sh [output.json]
set -euo pipefail
cd "$(dirname "$0")/.."

REQUESTS='
{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}
{"jsonrpc":"2.0","id":"get_worker_status","method":"tools/call","params":{"name":"get_worker_status"}}
{"jsonrpc":"2.0","id":"get_account_state","method":"tools/call","params":{"name":"get_account_state"}}
{"jsonrpc":"2.0","id":"get_open_positions","method":"tools/call","params":{"name":"get_open_positions"}}
{"jsonrpc":"2.0","id":"get_pending_orders","method":"tools/call","params":{"name":"get_pending_orders"}}
{"jsonrpc":"2.0","id":"get_risk_state","method":"tools/call","params":{"name":"get_risk_state"}}
{"jsonrpc":"2.0","id":"get_current_signal_state","method":"tools/call","params":{"name":"get_current_signal_state"}}
{"jsonrpc":"2.0","id":"get_last_decisions","method":"tools/call","params":{"name":"get_last_decisions","arguments":{"limit":50}}}
{"jsonrpc":"2.0","id":"explain_rejected_signal","method":"tools/call","params":{"name":"explain_rejected_signal","arguments":{"limit":20}}}
{"jsonrpc":"2.0","id":"explain_trade","method":"tools/call","params":{"name":"explain_trade"}}
{"jsonrpc":"2.0","id":"get_recent_trades","method":"tools/call","params":{"name":"get_recent_trades","arguments":{"limit":100}}}
{"jsonrpc":"2.0","id":"get_recent_trades_legacy","method":"tools/call","params":{"name":"get_recent_trades","arguments":{"limit":50,"source":"legacy"}}}
{"jsonrpc":"2.0","id":"get_strategy_metrics","method":"tools/call","params":{"name":"get_strategy_metrics"}}
{"jsonrpc":"2.0","id":"get_shadow_portfolios","method":"tools/call","params":{"name":"get_shadow_portfolios","arguments":{"limit":50}}}
{"jsonrpc":"2.0","id":"get_recent_errors","method":"tools/call","params":{"name":"get_recent_errors","arguments":{"limit":100,"include_raw_logs":true}}}
{"jsonrpc":"2.0","id":"get_runtime_config","method":"tools/call","params":{"name":"get_runtime_config"}}
{"jsonrpc":"2.0","id":"run_existing_backtest","method":"tools/call","params":{"name":"run_existing_backtest","arguments":{"version":"current","days":7}}}
'

SNAPSHOT=$(printf '%s\n' "$REQUESTS" \
  | uv run python -m orum_mcp.server 2>/dev/null \
  | uv run python -c "
import json, sys
snapshot = {}
for line in sys.stdin:
    msg = json.loads(line)
    result = msg.get('result') or {}
    if msg.get('id') == 0 or 'content' not in result:
        continue
    snapshot[msg['id']] = json.loads(result['content'][0]['text'])
print(json.dumps(snapshot, ensure_ascii=False, indent=1))
")

if [ $# -ge 1 ]; then
    printf '%s\n' "$SNAPSHOT" > "$1"
    echo "snapshot -> $1 ($(printf '%s' "$SNAPSHOT" | wc -c | tr -d ' ') bytes)" >&2
else
    printf '%s\n' "$SNAPSHOT"
fi
