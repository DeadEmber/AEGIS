"""Injectable clocks. Every engine receives a Clock; nothing calls datetime.now() directly.

This is what makes backtests/replays point-in-time and decisions reproducible."""

from __future__ import annotations

import datetime as dt
from typing import Protocol

UTC = dt.UTC


class Clock(Protocol):
    def now(self) -> dt.datetime: ...


class SystemClock:
    def now(self) -> dt.datetime:
        return dt.datetime.now(UTC)


class ManualClock:
    """Deterministic clock for tests and historical replay."""

    def __init__(self, start: dt.datetime):
        self._t = ensure_utc(start)

    def now(self) -> dt.datetime:
        return self._t

    def set(self, t: dt.datetime) -> None:
        t = ensure_utc(t)
        if t < self._t:
            raise ValueError("ManualClock cannot go backwards")
        self._t = t

    def advance(self, **kwargs: float) -> dt.datetime:
        self._t = self._t + dt.timedelta(**kwargs)
        return self._t


def ensure_utc(t: dt.datetime) -> dt.datetime:
    if t.tzinfo is None:
        raise ValueError("naive datetime rejected; AEGIS uses timezone-aware UTC only")
    return t.astimezone(UTC)


def parse_ts(value: object) -> dt.datetime | None:
    """Parse ISO-8601 / epoch seconds / epoch millis into aware UTC. None if absent/invalid."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, int | float):
            v = float(value)
            if v > 1e12:  # epoch millis
                v /= 1000.0
            return dt.datetime.fromtimestamp(v, UTC)
        if isinstance(value, str):
            s = value.strip().replace("Z", "+00:00")
            t = dt.datetime.fromisoformat(s)
            if t.tzinfo is None:
                t = t.replace(tzinfo=UTC)
            return t.astimezone(UTC)
    except (ValueError, OverflowError, OSError):
        return None
    return None


def iso(t: dt.datetime) -> str:
    return ensure_utc(t).isoformat()


def month_key(t: dt.datetime) -> str:
    return ensure_utc(t).strftime("%Y-%m")
