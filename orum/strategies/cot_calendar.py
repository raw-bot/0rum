"""2026 CFTC release calendar, verified 2026-09-05 against the official schedule.

https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm
Release timestamps use 15:30 America/New_York; dates of positions are not publication.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

_RELEASE_DAYS = {
    1: [5, 9, 16, 23, 30], 2: [6, 13, 20, 27], 3: [6, 13, 20, 27],
    4: [3, 10, 17, 24], 5: [1, 8, 15, 22, 29], 6: [5, 12, 22, 26],
    7: [6, 10, 17, 24, 31], 8: [7, 14, 21, 28], 9: [4, 11, 18, 25],
    10: [2, 9, 16, 23, 30], 11: [6, 16, 20, 30], 12: [4, 11, 18, 28],
}


def publication_at(report_date: str) -> datetime:
    report = date.fromisoformat(report_date)
    if report.year != 2026 or report.weekday() != 1:
        raise ValueError("COT report outside reviewed 2026 Tuesday calendar")
    earliest = report + timedelta(days=3)
    releases = [date(2026, month, day) for month, days in _RELEASE_DAYS.items() for day in days]
    release = next((day for day in releases if day >= earliest), None)
    if release is None or (release - report).days > 7:
        raise ValueError("COT release outside reviewed calendar")
    return datetime.combine(release, time(15, 30), ZoneInfo("America/New_York"))
