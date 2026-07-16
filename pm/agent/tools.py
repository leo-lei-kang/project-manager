"""The PM agent's tool surface — what the agent-under-test can do.

`AgentTools` is the small, explicit set of capabilities the agent acts through:
send/read Slack, read the Jira board, and read its calendar. It is a thin façade
over the existing world:

  * **Mutations** (sending Slack) route through :meth:`~pm.sim.engine.Engine.perform_action`
    so they consume sim-time and appear in the action log — the same contract the
    Jira tools use.
  * **Reads** are pure queries with no sim-time cost, returning plain JSON-ready
    dicts (what an LLM tool would consume), not internal model objects.

The agent's identity defaults to the cast's ``AGENT`` (``"pm"``).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pm.env.environment import Env
from pm.exceptions import ToolError
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.cast import AGENT
from pm.sim.events import SlackSendEvent


class AgentTools:
    """The capabilities the agent-under-test acts through against a run."""

    def __init__(self, env: Env, *, actor: str | None = None, send_cost: int = 1) -> None:
        self.env = env
        self.actor = actor or AGENT.id
        self.send_cost = send_cost
        repo = JiraRepository(env.store)
        repo.ensure_schema()
        self.jira = JiraApi(repo, env.engine)

    # -- Slack ---------------------------------------------------------------

    def send_slack(self, channel_id: str, body: str) -> dict[str, Any]:
        """Post a message to a channel; consumes ``send_cost`` sim-minutes.

        The tool *triggers an event*: the action's effect schedules a
        ``SlackSendEvent``, and the message lands through the event pipeline during
        the action's ``advance(cost)`` window (one tick of send latency) — the same
        path NPC messages take, so world reactions fire for it too.
        """
        engine = self.env.engine
        now = self.env.clock.now()
        # Preflight: the write is deferred into the event, so validate the channel
        # here to fail synchronously with a clean error.
        if self.env.store.db.query_one(
                "SELECT id FROM channel WHERE id = ?", (channel_id,)) is None:
            raise ToolError(
                f"could not send to channel {channel_id!r}: unknown channel",
                details={"channel_id": channel_id},
            )
        message_id = f"{channel_id}-m{self.env.store.count_messages(channel_id)}"
        engine.perform_action(
            actor=self.actor, cost=self.send_cost,
            effect=lambda: engine.schedule(SlackSendEvent(
                owner_id=self.actor, start_tick=now,
                payload={"message_id": message_id, "channel_id": channel_id, "body": body},
            )),
        )
        landed = [m for m in self.env.store.list_messages(channel_id) if m.id == message_id]
        if landed:
            return landed[0].model_dump()
        # Cost clamped to 0 at week end: the event is queued but hasn't landed yet.
        return {"id": message_id, "channel_id": channel_id, "sender_id": self.actor,
                "body": body, "sent_tick": None}

    def read_slack(self, channel_id: str) -> list[dict[str, Any]]:
        """Return the messages in a channel, oldest first (no sim-time cost)."""
        return [m.model_dump() for m in self.env.store.list_messages(channel_id)]

    # -- Jira board ----------------------------------------------------------

    def read_jira_board(self, project_id: str) -> dict[str, Any]:
        """Return a dashboard view of a project's board (no sim-time cost)."""
        issues = self.jira.search(project_id=project_id)
        return {
            "project_id": project_id,
            "issues": [i.model_dump() for i in issues],
            "counts_by_status": dict(Counter(i.status for i in issues)),
        }

    # -- Calendar ------------------------------------------------------------

    def read_calendar(self, person_id: str | None = None) -> list[dict[str, Any]]:
        """Return the meetings ``person_id`` (default: the agent) attends, in order."""
        who = person_id or self.actor
        return [m.model_dump() for m in self.env.store.list_meetings(attendee_id=who)]

    def read_transcripts(self) -> list[dict[str, Any]]:
        """Return the meeting transcripts available so far (no sim-time cost).

        A transcript becomes available when its meeting ends; each entry carries
        the meeting's title and time alongside the markdown body, so the agent
        can review what was said (status, open questions, unresolved decisions).
        """
        now = self.env.clock.now()
        titles = {m.id: m for m in self.env.store.list_meetings()}
        out = []
        for t in self.env.store.list_transcripts(available_by=now):
            meeting = titles.get(t.meeting_id)
            out.append({
                "meeting_id": t.meeting_id,
                "title": meeting.title if meeting else "",
                "start_tick": meeting.start_tick if meeting else None,
                "available_tick": t.available_tick,
                "body": t.body,
            })
        return out
