"""The "Two Engineers with PM" scenario — the cross-blocked pair, actively managed.

Same zero-slack board as :mod:`pm.scenarios.test_two_engineers` (two engineers,
40 h each, every odd task blocking the partner's next even task just-in-time),
plus the scripted PM (:mod:`pm.npc.scripted_pm`) working over Slack: it closes a
``when_asked`` member's finished work the same tick it appears (so cross handoffs
stay just-in-time) and directs a freestyle member's next pick. A persona mix that
misses the week unmanaged — e.g. ``alice=free_spirit,clare=heads_down`` — finishes
at exactly Fri 17:00 with the PM in the loop.

``agent_review_hook(env)`` builds the PM hook; the ``pm sim`` driver composes it
with the pickup hook automatically.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from pm.env.environment import RUNS_DIR, Env
from pm.npc.cast import AGENT
from pm.npc.cast import CAST as _FULL_CAST
from pm.npc.cast import seed_cast, with_personas
from pm.npc.persona import PERFECT, Persona
from pm.npc.scripted_pm import directive_pm_hook
from pm.scenarios.test_two_engineers import PROJECT_ID, _seed_board

if TYPE_CHECKING:
    from pm.sim.simulation import Simulation

SCENARIO = "test_two_engineers_with_pm"
CHANNEL = "eng"

CAST = [c for c in _FULL_CAST if c.id in ("alice", "clare")] + [AGENT]
MEMBERS = [c.id for c in CAST if c.kind == "member"]


def agent_review_hook(env: Env) -> Callable[["Simulation"], None]:
    """The scripted PM: same-tick Slack closes + directives for both engineers."""
    return directive_pm_hook(env, project_id=PROJECT_ID, members=MEMBERS, channel_id=CHANNEL)


def build(run_id: str = SCENARIO, *, seed: int = 42, root: Path = RUNS_DIR,
          force: bool = True,
          member_persona: Persona | Mapping[str, Persona] = PERFECT) -> Env:
    """Create the run: seed the engineers (+ the pm agent), channel, board; snapshot."""
    env = Env.make(SCENARIO, run_id, seed, force=force, root=root)
    seed_cast(env.store, cast=with_personas(CAST, member_persona))
    env.store.db.execute(
        "INSERT INTO channel (id, name, kind) VALUES (?, ?, 'channel')", (CHANNEL, CHANNEL))
    _seed_board(env)
    env.store.db.backup_to(Env.seed_path(run_id, root))
    return env


if __name__ == "__main__":
    build()
    print(f"Built scenario {SCENARIO!r} at runs/{SCENARIO}/ (the cross-blocked pair "
          "with a scripted PM closing and directing work over Slack).")
