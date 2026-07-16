"""Builder for chat events (SlackSend messages)."""

from __future__ import annotations

from pm.scenarios.builders.base import BuildContext, EventBuilder, Level, spread
from pm.sim.events import Event, EventType, SlackSendEvent


class ChatBuilder(EventBuilder):
    event_type = EventType.SLACK_SEND
    counts = {Level.FEW: 2, Level.FREQUENT: 8, Level.AGGRESSIVE: 20}

    def build(self, ctx: BuildContext, level: Level) -> list[Event]:
        senders = [*ctx.members, *ctx.stakeholders]
        out: list[Event] = []
        for i, t in enumerate(spread(self.count(level))):
            sender = senders[i % len(senders)]
            channel = ctx.channels[i % len(ctx.channels)]
            out.append(
                SlackSendEvent(
                    owner_id=sender, start_tick=t, duration=0,
                    payload={
                        "message_id": f"msg-{level.value}-{i}",
                        "channel_id": channel,
                        "body": f"chat message #{i + 1}",
                    },
                )
            )
        return out
