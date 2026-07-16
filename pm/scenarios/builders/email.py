"""Builder for email events (``EmailSendEvent``)."""

from __future__ import annotations

from pm.scenarios.builders.base import BuildContext, EventBuilder, Level, spread
from pm.sim.events import EmailSendEvent, Event, EventType


class EmailBuilder(EventBuilder):
    event_type = EventType.EMAIL_SEND
    counts = {Level.FEW: 1, Level.FREQUENT: 4, Level.AGGRESSIVE: 10}

    def build(self, ctx: BuildContext, level: Level) -> list[Event]:
        out: list[Event] = []
        for i, t in enumerate(spread(self.count(level))):
            sender = ctx.stakeholders[i % len(ctx.stakeholders)]
            out.append(
                EmailSendEvent(
                    owner_id=sender, initiator_id=sender, start_tick=t, duration=0,
                    payload={
                        "email_id": f"em-{level.value}-{i}",
                        "thread_id": f"th-{level.value}-{i}",
                        "subject": f"Status check #{i + 1}",
                        "body": "How's progress? Anything blocked?",
                        "to": [ctx.agent],
                    },
                )
            )
        return out
