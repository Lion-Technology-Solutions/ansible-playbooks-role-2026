"""Expand weekly academy wall times before converting each occurrence to UTC."""
from datetime import timedelta,timezone
from zoneinfo import ZoneInfo


def weekly_occurrences(local,zone,count):
    if local.tzinfo or not 1<=count<=26:raise ValueError('Choose 1–26 weekly sessions with a local start time.')
    values=[]
    for index in range(count):
        wall=local+timedelta(weeks=index);zoned=wall.replace(tzinfo=ZoneInfo(zone))
        if zoned.astimezone(timezone.utc).astimezone(ZoneInfo(zone)).replace(tzinfo=None)!=wall:raise ValueError('A recurring class falls in a daylight-saving gap. Choose another class time.')
        if zoned.utcoffset()!=wall.replace(tzinfo=ZoneInfo(zone),fold=1).utcoffset():raise ValueError('A recurring class falls in an ambiguous daylight-saving hour. Choose another class time.')
        values.append(zoned.astimezone(timezone.utc))
    return values
