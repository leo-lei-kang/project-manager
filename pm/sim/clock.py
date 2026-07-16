"""Simulated clock — a work-week calendar.

Sim-time is measured in integer **ticks** (one tick == one simulated minute) and is
stored in ``meta.current_tick`` — part of the single source of truth, surviving
save/resume, and decoupled from wall-clock: one agent action may cost many ticks
regardless of how long the model took.

The calendar is **work-hours only**. Time exists during 09:00–17:00 on weekdays; a
workday is 480 ticks and the five-day week is 2400 ticks. Tick 0 is Monday 09:00 and
tick 2400 is the Friday 17:00 terminal boundary. Because tick space is contiguous
work-minutes, 17:00 rolls straight into the next 09:00 — nights don't exist, so a delay
that crosses 5pm lands the next morning automatically.

    tick t == the minute starting at 09:00 + t work-minutes.
    valid work minutes: t in [0, 2400);  2400 == Fri 17:00 (week end).
"""

from __future__ import annotations

from pm.db.store import Store

TICKS_PER_HOUR = 60
WORK_START_HOUR = 9
WORK_END_HOUR = 17
MINUTES_PER_WORKDAY = (WORK_END_HOUR - WORK_START_HOUR) * TICKS_PER_HOUR  # 480
WORKDAYS = 5
TICKS_PER_WEEK = MINUTES_PER_WORKDAY * WORKDAYS  # 2400
WEEK_START_TICK = 0  # Monday 09:00
WEEK_END_TICK = TICKS_PER_WEEK  # 2400 == Friday 17:00 (exclusive work bound)
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")


def hours(n: int) -> int:
    """``n`` work-hours expressed in ticks."""
    return n * TICKS_PER_HOUR


def days(n: int) -> int:
    """``n`` workdays in ticks (a "day" == one 09:00-17:00 workday of 480 ticks)."""
    return n * MINUTES_PER_WORKDAY


def weekday(tick: int) -> str:
    """Weekday label (``Mon``..``Fri``) for ``tick``; clamped to the work week."""
    day = min(max(tick, 0) // MINUTES_PER_WORKDAY, WORKDAYS - 1)
    return WEEKDAYS[day]


def hh_mm(tick: int) -> tuple[int, int]:
    """(hour, minute) within the workday for ``tick`` (09:00-based)."""
    mod = min(max(tick, 0), TICKS_PER_WEEK) % MINUTES_PER_WORKDAY
    return WORK_START_HOUR + mod // TICKS_PER_HOUR, mod % TICKS_PER_HOUR


def is_within_week(tick: int) -> bool:
    """True while ``tick`` is inside the work week (``0 <= tick <= 2400``)."""
    return 0 <= tick <= TICKS_PER_WEEK


def format_tick(tick: int) -> str:
    """Human-friendly ``"Wed 14:30"`` rendering of a tick within the work week."""
    if tick >= TICKS_PER_WEEK:
        return f"{WEEKDAYS[-1]} {WORK_END_HOUR:02d}:00"  # Fri 17:00 terminal boundary
    hour, minute = hh_mm(tick)
    return f"{weekday(tick)} {hour:02d}:{minute:02d}"


class SimClock:
    def __init__(self, store: Store) -> None:
        self._store = store

    def now(self) -> int:
        """Current sim tick."""
        return self._store.get_tick()

    def advance(self, delta: int) -> int:
        """Advance the clock by ``delta`` ticks and return the new tick.

        ``delta`` must be non-negative; time never runs backwards.
        """
        if delta < 0:
            raise ValueError(f"cannot advance clock by negative delta {delta}")
        new_tick = self.now() + delta
        self._store.set_tick(new_tick)
        return new_tick

    def as_clock_str(self) -> str:
        """Work-calendar rendering of the current tick, e.g. ``"Wed 14:30"``."""
        return format_tick(self.now())

    # Kept as a staticmethod for callers that render an arbitrary tick.
    format_tick = staticmethod(format_tick)
