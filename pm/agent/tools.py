"""The PM agent's tool surface — what the agent-under-test can do.

`AgentTools` is the small, explicit set of capabilities the agent acts through:
send/read Slack, read the Jira board, and read its calendar. It is a thin façade
over the existing world:

  * **Mutations** (sending Slack) route through :meth:`~pm.sim.engine.Engine.perform_action`
    so they consume sim-time and appear in the action log — the same contract the
    Jira tools use.
  * **Reads** are pure queries with no sim-time cost, returning plain JSON-ready
    dicts (what an LLM tool would consume), not internal model objects.

The agent's identity defaults to the cast's ``AGENT`` (``"agent"``).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pm.env.environment import Env
from pm.exceptions import ToolError
from pm.jira.api import ALLOWED_TRANSITIONS, JiraApi
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

    def read_slack(self, channel_id: str, since_tick: int = 0) -> list[dict[str, Any]]:
        """Return the messages in a channel, oldest first (no sim-time cost).

        ``since_tick`` windows the history — pass the last tick you reviewed to
        pay only for the delta instead of the whole week's conversation.
        """
        return [m.model_dump() for m in self.env.store.list_messages(channel_id)
                if m.sent_tick >= since_tick]

    # -- Jira board ----------------------------------------------------------

    def create_jira_ticket(self, project_id: str, title: str, *,
                           issue_type: str = "task", estimate_minutes: int = 60,
                           assignee: str | None = None, priority: int = 2,
                           description: str = "") -> dict[str, Any]:
        """Create a Jira issue — e.g. an action item from a meeting transcript.

        A zero-cost board mutation (like all Jira writes, ``action_cost=0``);
        validation errors (unknown project/assignee, bad type) raise
        :class:`~pm.exceptions.ToolError`. Returns the created issue.
        """
        issue = self.jira.create_issue(
            project_id, issue_type, title, estimate_minutes=estimate_minutes,
            assignee=assignee, priority=priority, description=description,
            actor=self.actor)
        return issue.model_dump()

    def update_jira_status(self, key: str, status: str) -> dict[str, Any]:
        """Move an issue to ``status``, walking the legal workflow path
        (e.g. ``todo -> in_progress -> done``) — a zero-cost board mutation.

        Use it to make the board reflect reality (say, tickets filed from
        meeting notes that are already underway or finished). Unreachable
        targets (e.g. ``blocked``, which is derived) raise
        :class:`~pm.exceptions.ToolError`.
        """
        current = self.jira.get_issue(key).status
        # Shortest legal transition path via BFS over the workflow graph.
        paths: dict[str, list[str]] = {current: []}
        frontier = [current]
        while frontier and status not in paths:
            reached: list[str] = []
            for state in frontier:
                for nxt in sorted(ALLOWED_TRANSITIONS.get(state, set())):
                    if nxt not in paths:
                        paths[nxt] = paths[state] + [nxt]
                        reached.append(nxt)
            frontier = reached
        if status not in paths:
            raise ToolError(
                f"cannot move {key} from {current!r} to {status!r}",
                details={"key": key, "from": current, "to": status},
            )
        for step in paths[status]:
            self.jira.transition_issue(key, step, actor=self.actor)
        return self.jira.get_issue(key).model_dump()

    def read_jira_board(self, project_id: str) -> dict[str, Any]:
        """Return a dashboard view of a project's board (no sim-time cost).

        Token-lean by design: ``issues`` lists only the *open* work (todo, blocked,
        in_progress, in_review), each as the handful of fields a PM steers with;
        finished work shows up in ``counts_by_status`` rather than as full rows.
        """
        issues = self.jira.search(project_id=project_id)
        open_issues = [i for i in issues if i.status not in ("done", "cancelled")]
        return {
            "project_id": project_id,
            "issues": [{
                "key": i.id,
                "type": i.issue_type,
                "title": i.title,
                "status": i.status,
                "assignee": i.assignee_id,
                "priority": i.priority,
                "estimate_minutes": i.estimate_minutes,
                "remaining_minutes": i.remaining_minutes,
                "depends_on": i.depends_on,
            } for i in open_issues],
            "counts_by_status": dict(Counter(i.status for i in issues)),
        }

    # -- Calendar ------------------------------------------------------------

    def read_calendar(self, person_id: str | None = None) -> list[dict[str, Any]]:
        """Return the meetings ``person_id`` (default: the agent) attends, in order."""
        who = person_id or self.actor
        return [m.model_dump() for m in self.env.store.list_meetings(attendee_id=who)]

    def read_transcripts(self, since_tick: int = 0) -> list[dict[str, Any]]:
        """List the meeting transcripts available so far (no sim-time cost).

        A transcript becomes available when its meeting ends. This is the cheap
        index — meeting id, title, time, and a one-line ``preview`` — so a review
        doesn't re-pay for every full body each time; fetch a body with
        :meth:`read_transcript`. ``since_tick`` windows the list to transcripts
        that became available at or after it.
        """
        now = self.env.clock.now()
        titles = {m.id: m for m in self.env.store.list_meetings()}
        out = []
        for t in self.env.store.list_transcripts(available_by=now):
            if t.available_tick < since_tick:
                continue
            meeting = titles.get(t.meeting_id)
            first_line = next((ln for ln in t.body.splitlines() if ln.strip()), "")
            out.append({
                "meeting_id": t.meeting_id,
                "title": meeting.title if meeting else "",
                "start_tick": meeting.start_tick if meeting else None,
                "available_tick": t.available_tick,
                "preview": first_line[:120],
            })
        return out

    def read_transcript(self, meeting_id: str) -> dict[str, Any]:
        """Return one meeting's full transcript body (no sim-time cost)."""
        now = self.env.clock.now()
        for t in self.env.store.list_transcripts(available_by=now):
            if t.meeting_id == meeting_id:
                meeting = next(
                    (m for m in self.env.store.list_meetings() if m.id == meeting_id), None)
                return {
                    "meeting_id": t.meeting_id,
                    "title": meeting.title if meeting else "",
                    "start_tick": meeting.start_tick if meeting else None,
                    "available_tick": t.available_tick,
                    "body": t.body,
                }
        raise ToolError(
            f"no available transcript for meeting {meeting_id!r} (has it ended?)",
            details={"meeting_id": meeting_id},
        )
