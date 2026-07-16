"""A deterministic scripted PM — the Slack-directive stand-in for the LLM agent.

The PM reviews the board every tick and acts only through Slack, via the two
levers the world already reacts to (:func:`pm.npc.reactions._on_slack_send`):

* naming a person closes their finished-but-unclosed (``in_review``) work, and
* a "please pick up <KEY>" directive bumps that issue to priority 0 — the level
  even a freestyle persona works first (:func:`pm.npc.behavior._next_issue`).

Each message is applied synchronously — the message row is written and the
Slack reaction fired in the same tick — because the zero-slack scenario boards
cannot absorb even one tick of handoff lag. Closes run for *every* member
before any directive is computed, so a close that unblocks a partner's next
ticket is visible when that partner's directive is chosen. A real ``pm-agent``
LLM driving ``send_slack`` exercises exactly the same levers, just on its own
review cadence.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from pm.env.environment import Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.reactions import react
from pm.sim.events import SlackSendEvent
from pm.world.models import Message

if TYPE_CHECKING:
    from pm.sim.simulation import Simulation


def directive_pm_hook(
    env: Env, *, project_id: str, members: list[str], channel_id: str = "eng"
) -> Callable[["Simulation"], None]:
    """Build the scripted PM's per-tick hook (assumes ``pm`` + channel are seeded)."""
    api = JiraApi(JiraRepository(env.store), env.engine)
    names = {pid: (env.store.get_person(pid).name if env.store.get_person(pid) else pid)
             for pid in members}

    def post(now: int, pid: str, kind: str, body: str) -> None:
        """Write the Slack message and fire its reaction in the same tick."""
        env.store.add_message(Message(
            id=f"pm-{kind}-{now}-{pid}", channel_id=channel_id,
            sender_id="pm", body=body, sent_tick=now))
        react(env.engine, SlackSendEvent(
            owner_id="pm", start_tick=now,
            payload={"message_id": f"pm-{kind}-{now}-{pid}",
                     "channel_id": channel_id, "body": body}))

    def hook(sim: "Simulation") -> None:
        now = sim.clock.now()
        # Close pass first, for everyone: a close may unblock a partner's next
        # ticket, and the directive pass below must see the unblocked board.
        for pid in members:
            if api.search(project_id=project_id, assignee=pid, status="in_review"):
                post(now, pid, "close",
                     f"{names[pid]}: please close out your finished work")
        for pid in members:
            mine = api.search(project_id=project_id, assignee=pid)
            if any(i.status == "in_progress" for i in mine):
                continue  # mid-task; nothing to steer
            todo = [i for i in mine if i.status == "todo"]
            if not todo or any(i.priority <= 0 for i in todo):
                continue  # nothing workable, or already directed
            target = min(todo, key=lambda i: (i.priority, i.id))
            post(now, pid, "directive",
                 f"{names[pid]}: please pick up {target.id} next")

    return hook
