# Plan 06-09 Summary — Health Endpoint Risk Wiring

## Status: COMPLETE

## Commit
- `3a4cd87` — feat(06-09): wire health endpoint risk fields (circuit_breaker, open_positions, daily_pnl_pct)

## Test Results
```
3 passed in 0.27s
Full suite (excl. backtesting): 191 passed
```

## Changes to src/monitoring/health.py

**Added imports:**
```python
from src.risk.breaker import BreakerManager
from src.risk.gates import get_daily_pnl_pct, get_open_positions
```

**Risk wiring block (before return dict):**
```python
try:
    r_risk = aioredis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2)
    circuit_breaker_val = await BreakerManager(redis=r_risk).is_tripped()
    await r_risk.aclose()
    open_positions_val = await get_open_positions(db)
    daily_pnl_pct_val = float(await get_daily_pnl_pct(db))
except Exception as exc:
    logger.warning("health.risk_wiring.failed", error=str(exc))
    circuit_breaker_val = False
    open_positions_val = 0
    daily_pnl_pct_val = 0.0
```

**BreakerManager instantiation:** Created its own `decode_responses=True` Redis client (separate from the ping-only `r` client used for `redis_connected` check). This avoids sharing a ping-only connection with the breaker's ISO-8601 string reads.

**Placeholders replaced:** `circuit_breaker: False` → `circuit_breaker_val`, `open_positions: 0` → `open_positions_val`, `daily_pnl_pct: 0.0` → `daily_pnl_pct_val`.

## CLAUDE.md Known Gaps Resolved
- `circuit_breaker` — now live from `BreakerManager.is_tripped()`
- `open_positions` — now live from `get_open_positions(db)`
- `daily_pnl_pct` — now live from `get_daily_pnl_pct(db)`
- `signals_today` and `strategies_active` remain as placeholders (Phase 7 scope)
