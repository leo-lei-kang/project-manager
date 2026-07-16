"""The PM agent's tool surface — what the agent-under-test can do.

`AgentTools` is the small, explicit set of capabilities the agent acts through:
send/read Slack, read the Jira board, and read its calendar. It is a thin façade
over the existing world:

  * **Mutations** (sending Slack) route through :meth:`~pm.sim.engine.Engine.perform_action`
    so they consume sim-time and appear in the action log — the same contract the
    Jira tools use.
  * **Reads** are pure queries with no sim-time cost, returning plain JSON-ready
    dicts (what an LLM/MCP tool would consume), not internal model objects.

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
from pm.world.models import Message


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

        Routes through ``perform_action`` so the message lands at the current tick,
        the action is logged, and time then advances (firing due background events).
        """
        store = self.env.store
        n = store.count_messages(channel_id)
        message = Message(
            id=f"{channel_id}-m{n}",
            channel_id=channel_id,
            sender_id=self.actor,
            body=body,
            sent_tick=self.env.clock.now(),
        )
        try:
            self.env.engine.perform_action(
                actor=self.actor, cost=self.send_cost,
                effect=lambda: store.add_message(message),
            )
        except Exception as e:  # e.g. unknown channel / sender (FK) — surface cleanly
            raise ToolError(
                f"could not send to channel {channel_id!r}: {e}",
                details={"channel_id": channel_id},
            ) from e
        return message.model_dump()

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
