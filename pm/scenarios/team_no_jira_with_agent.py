"""The "Team, No Jira, with Agent" scenario — the notes-only week + an LLM PM.

Same week as :mod:`pm.scenarios.team_no_jira` — the Meeting Transcripts v1
project (25 tasks, DRIs, statuses) lives only in the meeting notes and the
informal ``task`` table, while the Jira board holds nothing but the
low-priority backlog epic — but here an LLM PM reviews once a day (09:00),
after each meeting, and whenever a Slack message names it — xavier (the CTO)
pushes for a status update daily at 16:00. The board trap is the test:
``read_jira_board`` shows a busy-and-green backlog all week, and only a PM
that reads the transcripts (``read_transcripts``) sees the real project — and
can reconcile the board itself with the Jira write tools.

The review hook (:func:`pm.agent.hook.llm_review_hook`) hands the model the
agent tools; every model round-trip and tool call is logged with token usage to
``runs/<run_id>/agent.jsonl``.

``agent_review_hook(env)`` builds the LLM review hook — under ``pm sim`` it
needs ``OPENROUTER_API_KEY`` in ``.env`` (model from ``OPENROUTER_MODEL``,
default :data:`DEFAULT_MODEL`); tests inject a fake ``client``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pm.agent.hook import llm_review_hook
from pm.env.environment import RUNS_DIR, Env
from pm.npc.cast import CAST as _FULL_CAST
from pm.npc.cast import seed_cast
from pm.scenarios.project_board import PROJECT_ID as PROJECT_ID
from pm.scenarios.project_board import seed_backlog_epic, seed_project_board
from pm.scenarios.runner import schedule_cxo_pushes
from pm.scenarios.team_no_jira import _schedule_meetings, tick_hook
from pm.sim.clock import MINUTES_PER_WORKDAY

__all__ = ["agent_review_hook", "build", "tick_hook"]

if TYPE_CHECKING:
    from pm.sim.simulation import Simulation

SCENARIO = "team_no_jira_with_agent"
CHANNEL = "eng"
# One cadence review per workday (09:00); meetings ending and Slack messages
# naming the PM (e.g. xavier's daily 16:00 status push) trigger extra reviews.
REVIEW_PERIOD = MINUTES_PER_WORKDAY
DEFAULT_MODEL = "anthropic/claude-opus-4.8"

PROMPT = (
    f"You are the PM for project '{PROJECT_ID}'. Review the Jira board with "
    "read_jira_board AND the meeting transcripts with read_transcripts — on this "
    "project the board may not tell the whole story; the notes track tasks the "
    "board never sees. Read only transcripts you have not read before (the "
    "listing marks read ones), then immediately append_memory the takeaways — "
    "each transcript can be read once, and your memory is shown to you at every "
    "review. If the board does not match the notes, fix the board "
    "yourself: file the missing work with create_jira_ticket, carrying over the "
    "title, assignee, AND estimate_minutes from the notes' Estimate column — "
    "every ticket you file must have its estimate set — and set each ticket's "
    "real status with update_jira_status; never re-file a ticket that already "
    "exists. If the "
    f"record also needs surfacing, post at most ONE short '{CHANNEL}' Slack "
    "message summarizing the real status (who owns what, what is done or at "
    "risk); if nothing new has happened, post nothing. When a stakeholder asks "
    "you for a status update in Slack, reply in the channel with a brief, "
    "concrete status. Then reply with a one-line summary and no tool call."
)

# The five implementers + the pm agent (the Slack sender) + xavier (the CxO
# who pushes for a daily status). MEMBERS drives the pickup hook; the agent
# and xavier have works=False so the pickup hook skips them.
CAST = [c for c in _FULL_CAST if c.kind == "member" or c.id in ("agent", "xavier")]
MEMBERS = [c.id for c in CAST if c.kind == "member"]


def agent_review_hook(
    env: Env, *, client: Any = None, model: str | None = None,
) -> Callable[["Simulation"], None]:
    """Build the LLM PM's review hook: every ``REVIEW_PERIOD`` ticks, one agent loop.

    ``client``/``model`` default to the OpenRouter client from ``.env`` and
    ``OPENROUTER_MODEL`` (falling back to ``DEFAULT_MODEL``); tests pass a fake
    client. Activity lands in ``runs/<run_id>/agent.jsonl``.
    """
    return llm_review_hook(
        env,
        model=model or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL),
        prompt=PROMPT,
        client=client,
        period=REVIEW_PERIOD,
    )


def build(run_id: str = SCENARIO, *, seed: int = 42, root: Path = RUNS_DIR,
          force: bool = True) -> Env:
    """Create the run: the team (+ the pm agent), the empty board, the meeting week."""
    env = Env.make(SCENARIO, run_id, seed, force=force, root=root)
    seed_cast(env.store, cast=CAST)
    env.store.db.execute(
        "INSERT INTO channel (id, name, kind) VALUES (?, ?, 'channel')", (CHANNEL, CHANNEL))
    seed_project_board(env, jira_ids=())
    seed_backlog_epic(env)
    _schedule_meetings(env)
    schedule_cxo_pushes(env, CHANNEL)
    env.store.db.backup_to(Env.seed_path(run_id, root))
    return env


if __name__ == "__main__":
    build()
    print(f"Built scenario {SCENARIO!r} at runs/{SCENARIO}/ (the notes-only week "
          "while the LLM pm agent reviews daily, after meetings, and on mentions).")
