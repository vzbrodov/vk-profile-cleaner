from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date
    timezone: str = 'Europe/Moscow'

    def bounds(self):
        if self.start > self.end:
            raise ValueError('Начало периода позже конца.')
        zone = ZoneInfo(self.timezone)
        return (datetime.combine(self.start, time(), zone).timestamp(),
                datetime.combine(self.end + timedelta(days=1), time(), zone).timestamp())

    def contains(self, timestamp):
        start, end = self.bounds()
        return start <= timestamp < end


@dataclass(frozen=True)
class Dialog:
    peer_id: int
    title: str


class CleanerError(Exception):
    def __init__(self, message, code=None, unknown=False):
        super().__init__(message)
        self.code = code
        self.unknown = unknown


class Cancelled(CleanerError):
    def __init__(self):
        super().__init__('Операция отменена.')
