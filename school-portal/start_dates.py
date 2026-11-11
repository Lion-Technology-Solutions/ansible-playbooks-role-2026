"""Published enrollment intakes, separate from individual lesson scheduling."""
from datetime import date,timedelta

ANALYTICS_FIRST=date(2026,1,1)
ANALYTICS_LAST=date(2027,12,31)


def analytics_starts(today):
    day=max(today,ANALYTICS_FIRST)
    day+=timedelta(days=(5-day.weekday())%7)
    starts=[]
    while day<=ANALYTICS_LAST:
        starts.append(day);day+=timedelta(days=7)
    return starts
