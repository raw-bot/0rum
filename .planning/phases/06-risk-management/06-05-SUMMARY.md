# Plan 06-05 Summary — Risk Gates (RISK-01/02/03 + Health Helpers)

## Status: COMPLETE

## Commits
- RED:   `817401a` — test(06-05): RED — failing gate tests for RISK-01/02/03
- GREEN: `a0f9f7c` — feat(06-05): GREEN — implement risk gates RISK-01/02/03 + health helpers

## Test Results
```
9 passed in 0.19s
```

## Function Signatures

```python
async def evaluate_daily_loss(session: AsyncSession, daily_loss_limit: float) -> tuple[bool, float]
async def evaluate_max_positions(session: AsyncSession, max_positions: int) -> tuple[bool, int]
async def count_same_direction_open(session: AsyncSession, direction: str) -> int
async def get_open_positions(session: AsyncSession) -> int
async def get_daily_pnl_pct(session: AsyncSession) -> float
```

## SQL Snippets

**RISK-01 / get_daily_pnl_pct:**
```python
select(func.coalesce(func.sum(TradeORM.pnl_pct), 0)).where(
    TradeORM.closed_at >= func.date_trunc("day", func.timezone("UTC", func.now())),
    TradeORM.status == "CLOSED",
)
```

**RISK-02 / get_open_positions:**
```python
select(func.count()).select_from(TradeORM).where(TradeORM.status == "OPEN")
```

**RISK-03:**
```python
select(func.count()).select_from(TradeORM).where(
    TradeORM.status == "OPEN", TradeORM.direction == direction
)
```

## Pitfall Mitigations

| Grep | Result |
|------|--------|
| `grep -c "AsyncSessionLocal" src/risk/gates.py` | 0 — Pitfall 4 honored |
| `grep -c "func.coalesce(func.sum" src/risk/gates.py` | 2 — Pitfall 3 (NULL SUM) mitigated in both SUM callers |
| `grep -c "func.date_trunc" src/risk/gates.py` | 2 — D-14 UTC-day boundary computed server-side |
| `grep -c "async def " src/risk/gates.py` | 5 — all five functions present |
