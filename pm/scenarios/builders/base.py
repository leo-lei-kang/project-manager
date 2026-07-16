"""Per-event scenario builders — shared plumbing.

Each concrete builder owns exactly one :class:`~pm.sim.events.EventType` and, given
a :class:`BuildContext` and an intensity :class:`Level`, returns a list of *unscheduled*
``Event`` instances (a pure factory). The generator schedules them. Intensity maps to a
per-week count via each builder's ``counts`` table; ``spread`` lays those events out
across the Mon-Fri work window.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pm.sim.clock import MINUTES_PER_WORKDAY, WEEK_END_TICK
from pm.sim.events import Event, EventType


class Level(str, Enum):
    FEW = "few"
    FREQUENT = "frequent"
    AGGRESSIVE = "aggressive"


# Ascending intensity — the generator walks this order ("start with less frequent").
LEVEL_ORDER: list[Level] = [Level.FEW, Level.FREQUENT, Level.AGGRESSIVE]


def at(day: int, hour: int, minute: int = 0) -> int:
    """Tick for (weekday 0=Mon..4=Fri, hour, minute) on the work-hours calendar."""
    return day * MINUTES_PER_WORKDAY + (hour - 9) * 60 + minute


# Leave headroom so durative events (meetings, work) can finish before the horizon.
_SPREAD_END = WEEK_END_TICK - 120


def spread(n: int) -> list[int]:
    """``n`` start-ticks spread evenly across the work week, all < ``WEEK_END_TICK``."""
    if n <= 0:
        return []
    if n == 1:
        return [_SPREAD_END // 2]
    step = _SPREAD_END // n
    return [i * step for i in range(n)]


@dataclass
class BuildContext:
    """Seeded ids the builders reference (all already present in the world)."""

    members: list[str]        # implementer person ids
    stakeholders: list[str]   # manager/exec person ids
    agent: str                # the PM person id
    channels: list[str]       # chat channel ids
    issue_keys: list[str]     # jira issues (todo) available to work


class EventBuilder:
    """Base: one event type, a per-level count, and a build() factory."""

    event_type: EventType
    counts: dict[Level, int]

    def count(self, level: Level) -> int:
        return self.counts[level]

    def build(self, ctx: BuildContext, level: Level) -> list[Event]:
        raise NotImplementedError
