"""The "Single Engineer with PM" scenario — a free-spirit engineer, actively directed.

Same overloaded solo board as :mod:`pm.scenarios.test_single_engineer` (12 tasks,
7 high-priority launch blockers + 5 backlog, 60 h in a 40-h week) with alice as a
:data:`~pm.npc.persona.FREE_SPIRIT` — but here the scripted PM *directs* her over
Slack (:mod:`pm.npc.scripted_pm`): each pick, it posts "please pick up <KEY>" for
her highest-priority open ticket, which the Slack reaction bumps to priority 0 —
the level even a freestyle persona works first. Unlike the visibility-only
:mod:`~pm.scenarios.test_single_engineer_with_agent`, the directives change what
ships: all seven launch blockers complete.

``agent_review_hook(env)`` builds the PM hook; the ``pm sim`` driver composes it
with the pickup hook automatically.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from pm.env.environment import RUNS_DIR, Env
from pm.npc.cast import CAST as _FULL_CAST
from pm.npc.cast import seed_cast, with_personas
from pm.npc.persona import FREE_SPIRIT, Persona
from pm.npc.scripted_pm import directive_pm_hook
from pm.scenarios.test_single_engineer import PROJECT_ID, _seed_board

if TYPE_CHECKING:
    from pm.sim.simulation import Simulation

SCENARIO = "test_single_engineer_with_pm"
CHANNEL = "eng"

# alice (the implementer) + the pm agent (the Slack sender).
CAST = [c for c in _FULL_CAST if c.id in ("alice", "pm")]
MEMBERS = [c.id for c in CAST if c.kind == "member"]


def agent_review_hook(env: Env) -> Callable[["Simulation"], None]:
    """The scripted PM: same-tick Slack directives steering alice's picks."""
    return directive_pm_hook(env, project_id=PROJECT_ID, members=MEMBERS, channel_id=CHANNEL)


def build(run_id: str = SCENARIO, *, seed: int = 42, root: Path = RUNS_DIR,
          force: bool = True,
          member_persona: Persona | Mapping[str, Persona] = FREE_SPIRIT) -> Env:
    """Create the run: seed alice (+ the pm agent), channel, board; snapshot."""
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
          "whose picks the scripted PM directs over Slack — the launch blockers ship).")
