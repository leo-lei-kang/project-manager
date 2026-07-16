"""The "Single Engineer with Agent" scenario — a free-spirit engineer + a PM agent.

Same overloaded solo board as :mod:`pm.scenarios.test_single_engineer` (12 tasks,
7 high-priority launch blockers + 5 backlog, 60 h in a 40-h week), but here alice
works as a :data:`~pm.npc.persona.FREE_SPIRIT` — picking tickets at random, ignoring
priority — so the launch blockers are at risk of being left over.

The agent-under-test (``pm``) acts *during* the run: every four sim-hours it reviews
the board (:meth:`~pm.agent.tools.AgentTools.read_jira_board`) and, when high-priority
tickets are still open, posts a Slack message to ``#eng`` highlighting them — but only
when there is something new to say (the open set is non-empty and has changed since its
last post). It raises visibility; it does not reorder the engineer's random picking.

``build(member_persona=...)`` seeds alice with the given persona (default:
:data:`~pm.npc.persona.FREE_SPIRIT`). ``agent_review_hook(env)`` builds the PM's
per-tick review hook; compose it with a pickup hook to run the full interaction (the
``pm sim`` driver does this automatically).
"""

from __future__ import annotations

from collections.abc import Mapping

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pm.agent.tools import AgentTools
from pm.env.environment import RUNS_DIR, Env
from pm.npc.cast import CAST as _FULL_CAST
from pm.npc.cast import seed_cast, with_personas
from pm.npc.persona import FREE_SPIRIT, Persona
from pm.scenarios.test_single_engineer import HIGH_PRIORITY, PROJECT_ID, _seed_board
from pm.sim.events import SlackSendEvent

if TYPE_CHECKING:
    from pm.sim.simulation import Simulation

SCENARIO = "test_single_engineer_with_agent"
CHANNEL = "eng"
REVIEW_PERIOD = 240  # review every four sim-hours (60 min * 4)

# alice (the implementer) + the pm agent (the Slack sender). MEMBERS drives the
# pickup hook; the agent has works=False so the pickup hook skips it.
CAST = [c for c in _FULL_CAST if c.id in ("alice", "pm")]
MEMBERS = [c.id for c in CAST if c.kind == "member"]


def agent_review_hook(env: Env) -> Callable[["Simulation"], None]:
    """Build the PM's review hook: every ``REVIEW_PERIOD`` ticks, highlight open blockers.

    Reads the board through the agent's own tool and posts a Slack highlight only when
    it is needed — high-priority tickets are still open *and* the open set changed since
    the last post (so an unchanged board is not re-nagged every four hours). The post is
    scheduled as a :class:`~pm.sim.events.SlackSendEvent` (clock-safe) rather than via
    ``AgentTools.send_slack``, which would advance the clock mid-loop.
    """
    tools = AgentTools(env)  # actor defaults to the pm agent
    last_posted: set[str] = set()

    def hook(sim: "Simulation") -> None:
        nonlocal last_posted
        now = sim.clock.now()
        if now % REVIEW_PERIOD != 0:
            return
        board = tools.read_jira_board(PROJECT_ID)
        open_high = {
            i["id"] for i in board["issues"]
            if i["priority"] == HIGH_PRIORITY and i["status"] != "done"
        }
        if not open_high or open_high == last_posted:
            return  # nothing new to highlight
        last_posted = open_high
        body = "High-priority still open: " + ", ".join(sorted(open_high)) + " — please prioritize."
        sim.schedule(SlackSendEvent(
            owner_id="pm", start_tick=now,
            payload={"message_id": f"pm-highlight-{now}", "channel_id": CHANNEL, "body": body},
        ))

    return hook


def build(run_id: str = SCENARIO, *, seed: int = 42, root: Path = RUNS_DIR,
          force: bool = True, member_persona: Persona | Mapping[str, Persona] = FREE_SPIRIT) -> Env:
    """Create the run: seed alice (+ the pm agent) and the overloaded board, snapshot."""
    env = Env.make(SCENARIO, run_id, seed, force=force, root=root)
    seed_cast(env.store, cast=with_personas(CAST, member_persona))
    env.store.db.execute(
        "INSERT INTO channel (id, name, kind) VALUES (?, ?, 'channel')", (CHANNEL, CHANNEL))
    _seed_board(env)
    env.store.db.backup_to(Env.seed_path(run_id, root))
    return env


if __name__ == "__main__":
    build()
    print(f"Built scenario {SCENARIO!r} at runs/{SCENARIO}/ (a free-spirit engineer "
          "while the pm agent reviews the board and highlights high-priority tickets).")
