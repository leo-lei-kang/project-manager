"""Builder for out-of-office spans (``OOOEvent``)."""

from __future__ import annotations

from pm.scenarios.builders.base import BuildContext, EventBuilder, Level, spread
from pm.sim.clock import days, hours
from pm.sim.events import Event, EventType, OOOEvent

# Alternating span lengths: "a few hours" and "a few days".
_DURATIONS = [hours(3), days(1), hours(2), days(2), hours(4)]


class OOOBuilder(EventBuilder):
    event_type = EventType.OOO
    counts = {Level.FEW: 1, Level.FREQUENT: 3, Level.AGGRESSIVE: 5}

    def build(self, ctx: BuildContext, level: Level) -> list[Event]:
        out: list[Event] = []
        for i, t in enumerate(spread(self.count(level))):
            owner = ctx.members[i % len(ctx.members)]
            out.append(
                OOOEvent(
                    owner_id=owner,
                    start_tick=t,
                    duration=_DURATIONS[i % len(_DURATIONS)],
                    payload={"reason": "out of office"},
                )
            )
        return out
