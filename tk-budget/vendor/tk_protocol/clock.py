"""Injectable clock (local time, naive datetimes)."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now()


class FixedClock:
    def __init__(self, dt: datetime) -> None:
        self._dt = dt

    def now(self) -> datetime:
        return self._dt

    def set(self, dt: datetime) -> None:
        self._dt = dt
