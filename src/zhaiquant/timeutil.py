from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .types import SHANGHAI


MORNING = (time(9, 30), time(11, 30))
AFTERNOON = (time(13, 0), time(15, 0))


def parse_clock(value: str) -> time:
    return time.fromisoformat(value)


def in_continuous_session(moment: datetime) -> bool:
    local = moment.astimezone(SHANGHAI).time().replace(tzinfo=None)
    return MORNING[0] <= local <= MORNING[1] or AFTERNOON[0] <= local <= AFTERNOON[1]


def trading_seconds_between(start: datetime, end: datetime) -> float:
    if end <= start:
        return 0.0
    start = start.astimezone(SHANGHAI)
    end = end.astimezone(SHANGHAI)
    total = 0.0
    current: date = start.date()
    while current <= end.date():
        if current.weekday() < 5:
            for session_start, session_end in (MORNING, AFTERNOON):
                left = datetime.combine(current, session_start, tzinfo=SHANGHAI)
                right = datetime.combine(current, session_end, tzinfo=SHANGHAI)
                overlap_start = max(start, left)
                overlap_end = min(end, right)
                if overlap_end > overlap_start:
                    total += (overlap_end - overlap_start).total_seconds()
        current += timedelta(days=1)
    return total
