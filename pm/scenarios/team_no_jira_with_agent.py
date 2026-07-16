"""The "Team, No Jira, with Agent" scenario — the notes-only week + an LLM PM.

Same week as :mod:`pm.scenarios.team_no_jira` — the Meeting Transcripts v1
project (25 tasks, DRIs, statuses) lives only in the meeting notes and the
informal ``task`` table, and the Jira board never holds a ticket — but here an
LLM PM reviews the run every four sim-hours. The board trap is the test:
``read_jira_board`` says nothing is happening all week, and only a PM that
reads the transcripts (``read_transcripts``) sees the real project. With no
board work to steer, the Slack levers move nothing; the PM's job is visibility.

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
from pm.scenarios.project_board import seed_project_board
from pm.scenarios.team_no_jira import _schedule_meetings

if TYPE_CHECKING:
    from pm.sim.simulation import Simulation

SCENARIO = "team_no_jira_with_agent"
CHANNEL = "eng"
REVIEW_PERIOD = 240  # review every four sim-hours (60 min * 4)
DEFAULT_MODEL = "anthropic/claude-opus-4.8"

PROMPT = (
    f"You are the PM for project '{PROJECT_ID}'. Review the Jira board with "
    "read_jira_board AND the meeting transcripts with read_transcripts — on this "
    "project the board may not tell the whole story; the notes track tasks the "
    "board never sees. If the board does not match the notes, fix the board "
    "yourself: file the missing work with create_jira_ticket (title, assignee, "
    "estimate from the notes) and set each ticket's real status with "
    "update_jira_status — never re-file a ticket that already exists. If the "
    f"record also needs surfacing, post at most ONE short '{CHANNEL}' Slack "
    "message summarizing the real status (who owns what, what is done or at "
    "risk); if nothing new has happened, post nothing. Then reply with a "
    "one-line summary and no tool call."
)

# The five implementers + the pm agent (the Slack sender). MEMBERS drives the
# pickup hook; the agent has works=False so the pickup hook skips it.
CAST = [c for c in _FULL_CAST if c.kind == "member" or c.id == "agent"]
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
    _schedule_meetings(env)
    env.store.db.backup_to(Env.seed_path(run_id, root))
    return env


if __name__ == "__main__":
    build()
    print(f"Built scenario {SCENARIO!r} at runs/{SCENARIO}/ (the notes-only week "
          "while the LLM pm agent reviews board + transcripts every four sim-hours).")
