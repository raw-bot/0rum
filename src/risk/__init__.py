"""Risk management package — pre-execution gates, ATR sizer, Redis circuit breaker.

Public surface (Plan 08 imports `from src.risk import RiskGateRunner`):
  - RiskGateRunner       (orchestrator; pipeline calls .evaluate per candidate)
  - BreakerManager       (Redis state machine; Phase 7 calls record_stop/record_win)
  - register_alert_hook  (generic alert hook surface for startup-registered adapters)

Per CONTEXT D-02: pipeline imports ONLY through these names.
"""

from src.risk.breaker import BreakerManager
from src.risk.hooks import register_alert_hook
from src.risk.runner import RiskGateRunner

__all__ = ["BreakerManager", "RiskGateRunner", "register_alert_hook"]
