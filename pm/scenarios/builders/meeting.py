"""Builder for meeting events (``MeetingEvent``)."""

from __future__ import annotations

from pm.scenarios.builders.base import BuildContext, EventBuilder, Level, spread
from pm.sim.events import Event, EventType, MeetingEvent


class MeetingBuilder(EventBuilder):
    event_type = EventType.MEETING
    counts = {Level.FEW: 1, Level.FREQUENT: 5, Level.AGGRESSIVE: 12}

    def build(self, ctx: BuildContext, level: Level) -> list[Event]:
        attendees = [ctx.agent, *ctx.members]
        out: list[Event] = []
        for i, t in enumerate(spread(self.count(level))):
            out.append(
                MeetingEvent(
                    owner_id=ctx.agent, initiator_id=ctx.agent,
                    start_tick=t, duration=30,
                    payload={
                        "meeting_id": f"mtg-{level.value}-{i}",
                        "kind": "standup",
                        "title": f"Sync #{i + 1}",
                        "attendees": attendees,
                        "transcript_id": f"tr-{level.value}-{i}",
                        "transcript_body": f"Sync #{i + 1} notes.",
                    },
                )
            )
        return out
