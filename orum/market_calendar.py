"""Pinned 2026 Nasdaq sessions, including DST, holidays and early closes.

Source checked 2026-09-05:
https://www.nasdaq.com/market-activity/stock-market-holiday-schedule
Unknown years fail closed until their official calendar is reviewed.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
HOLIDAYS_2026 = frozenset({"2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"})
EARLY_2026 = frozenset({"2026-11-27", "2026-12-24"})


def session_bounds(day: date) -> tuple[datetime, datetime] | None:
    if day.year != 2026:
        raise ValueError(f"unreviewed Nasdaq calendar year {day.year}")
    if day.weekday() >= 5 or day.isoformat() in HOLIDAYS_2026:
        return None
    return (datetime.combine(day, time(9, 30), NEW_YORK),
            datetime.combine(day, time(13 if day.isoformat() in EARLY_2026 else 16), NEW_YORK))


def latest_closed_bar(now: datetime, interval_minutes: int = 5) -> int:
    local = now.astimezone(NEW_YORK)
    day = local.date()
    for _ in range(10):
        bounds = session_bounds(day)
        if bounds:
            start, end = bounds
            cutoff = min(local, end)
            count = int((cutoff - start).total_seconds() // (interval_minutes * 60))
            if count >= 1:
                return int((start + timedelta(minutes=(count - 1) * interval_minutes)).timestamp() * 1000)
        day -= timedelta(days=1)
    raise ValueError("no recent Nasdaq session")
