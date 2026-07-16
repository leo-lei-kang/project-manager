"""The "Two Engineers, Mixed, with Agent" scenario — the stalling pair + an LLM PM.

Same zero-slack cross-blocked board as :mod:`pm.scenarios.two_engineers`
(alice :data:`~pm.npc.persona.FREE_SPIRIT`, clare :data:`~pm.npc.persona.HEADS_DOWN`),
so unmanaged the just-in-time handoffs stall and the week misses — here an LLM PM
acts *during* the run to rescue it.

The agent-under-test (``pm``) reviews every four sim-hours: the review hook
(:func:`pm.agent.hook.llm_review_hook`) hands the model the agent tools; it reads
the board and may post one short ``#eng`` message using its two levers (naming a
person closes their in-review work; "please pick up <KEY>" preempts the assignee
onto that ticket, resuming the dropped one afterwards — both take effect when the
named person reads the message, within the hour). Every model round-trip
and tool call is logged with token usage to ``runs/<run_id>/agent.jsonl``.

``build(member_persona=...)`` seeds the engineers with the given personas
(default: the stalling mix). ``agent_review_hook(env)`` builds the LLM review
hook — under ``pm sim`` it needs ``OPENROUTER_API_KEY`` in ``.env`` (model from
``OPENROUTER_MODEL``, default :data:`DEFAULT_MODEL`); tests inject a fake
``client``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pm.agent.hook import llm_review_hook
from pm.env.environment import RUNS_DIR, Env
from pm.npc.cast import CAST as _FULL_CAST
from pm.npc.cast import seed_cast, with_personas
from pm.npc.persona import Persona
from pm.scenarios.two_engineers import MIXED, PROJECT_ID, _seed_board

if TYPE_CHECKING:
    from pm.sim.simulation import Simulation

SCENARIO = "two_engineers_with_agent"
CHANNEL = "eng"
REVIEW_PERIOD = 240  # review every four sim-hours (60 min * 4)
DEFAULT_MODEL = "openai/gpt-5.5-pro"

PROMPT = (
    f"You are the PM for project '{PROJECT_ID}'. Review the Jira board with "
    f"read_jira_board and the '{CHANNEL}' Slack channel. You have two levers, both "
    f"via one Slack message to '{CHANNEL}' that take effect when the person reads "
    "it (within the hour): naming a person (e.g. alice) makes them close "
    "their in-review work, and the exact phrase 'please pick up <TICKET-KEY>' makes "
    "them drop their current ticket and work yours first (the dropped ticket "
    "resumes afterwards). If a high-priority ticket is open and not "
    "being worked next, post ONE short message using a lever; if nothing needs "
    "steering, post nothing. Then reply with a one-line summary and no tool call."
)

# alice + clare (the implementers) + the pm agent (the Slack sender). MEMBERS
# drives the pickup hook; the agent has works=False so the pickup hook skips it.
CAST = [c for c in _FULL_CAST if c.id in ("alice", "clare", "pm")]
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
          force: bool = True,
          member_persona: Persona | Mapping[str, Persona] = MIXED) -> Env:
    """Create the run: seed the engineers (+ the pm agent) and the board, snapshot."""
    env = Env.make(SCENARIO, run_id, seed, force=force, root=root)
    seed_cast(env.store, cast=with_personas(CAST, member_persona))
    env.store.db.execute(
        "INSERT INTO channel (id, name, kind) VALUES (?, ?, 'channel')", (CHANNEL, CHANNEL))
    _seed_board(env)
    env.store.db.backup_to(Env.seed_path(run_id, root))
    return env


if __name__ == "__main__":
    build()
    print(f"Built scenario {SCENARIO!r} at runs/{SCENARIO}/ (a stalling mixed-persona "
          "pair while the LLM pm agent reviews the board every four sim-hours).")
