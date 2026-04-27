"""Risk management package — pre-execution gates, ATR sizer, Redis circuit breaker.

Public surface (re-exports added once the modules ship in later plans):
  - RiskGateRunner       (from src.risk.runner)
  - BreakerManager       (from src.risk.breaker)
  - register_alert_hook  (from src.risk.hooks)

Per CONTEXT D-02: pipeline imports ONLY through these names, never gate-internal helpers.
"""
