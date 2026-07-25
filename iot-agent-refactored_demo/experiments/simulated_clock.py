from __future__ import annotations

from datetime import datetime, timedelta


class SimulatedClock:
    def __init__(self, start: datetime):
        self.now = start

    def advance(self, *, days: int = 0, hours: int = 0, minutes: int = 0):
        self.now += timedelta(days=days, hours=hours, minutes=minutes)
        return self.now

    def set(self, value: datetime):
        self.now = value
        return self.now

