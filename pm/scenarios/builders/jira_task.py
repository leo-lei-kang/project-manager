"""Builder for Jira ticket-work events (``JiraTicketEvent``)."""

from __future__ import annotations

from pm.scenarios.builders.base import BuildContext, EventBuilder, Level, spread
from pm.sim.events import Event, EventType, JiraTicketEvent

_WORK_DURATION = 90  # minutes a work span occupies


class JiraTaskBuilder(EventBuilder):
    event_type = EventType.JIRA_TICKET
    counts = {Level.FEW: 2, Level.FREQUENT: 6, Level.AGGRESSIVE: 12}

    def build(self, ctx: BuildContext, level: Level) -> list[Event]:
        # One work event per distinct issue; capped at the seeded issue pool.
        n = min(self.count(level), len(ctx.issue_keys))
        out: list[Event] = []
        for i, t in enumerate(spread(n)):
            owner = ctx.members[i % len(ctx.members)]
            out.append(
                JiraTicketEvent(
                    owner_id=owner, start_tick=t, duration=_WORK_DURATION,
                    payload={"issue_key": ctx.issue_keys[i]},
                )
            )
        return out
