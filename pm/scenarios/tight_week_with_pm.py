"""The "Tight Week with PM" scenario — the saturated team week, actively managed.

Same capacity-saturated board and meeting fabric as :mod:`pm.scenarios.tight_week`
(66 tasks tiling five members' meeting-free calendars exactly), plus the scripted
PM (:mod:`pm.npc.scripted_pm`) working over Slack: same-tick closes keep a
``when_asked`` member's cross handoffs just-in-time, and directives pin a
freestyle member back onto their chronological ordinals. A persona mix that
cannot finish the week unmanaged completes at exactly Fri 17:00 with the PM in
the loop.

``agent_review_hook(env)`` builds the PM hook; the ``pm sim`` driver composes it
with the pickup hook automatically.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from pm.env.environment import RUNS_DIR, Env
from pm.npc.cast import AGENT, seed_cast, with_personas
from pm.npc.persona import PERFECT, Persona
from pm.npc.scripted_pm import directive_pm_hook
from pm.scenarios.tight_week import CAST as _TEAM_CAST
from pm.scenarios.tight_week import MEMBERS as MEMBERS
from pm.scenarios.tight_week import PROJECT_ID, _schedule_meetings, _seed_board

if TYPE_CHECKING:
    from pm.sim.simulation import Simulation

SCENARIO = "tight_week_with_pm"
CHANNEL = "eng"

CAST = [*_TEAM_CAST, AGENT]


def agent_review_hook(env: Env) -> Callable[["Simulation"], None]:
    """The scripted PM: same-tick Slack closes + directives for all five members."""
    return directive_pm_hook(env, project_id=PROJECT_ID, members=MEMBERS, channel_id=CHANNEL)


def build(run_id: str = SCENARIO, *, seed: int = 42, root: Path = RUNS_DIR,
          force: bool = True,
          member_persona: Persona | Mapping[str, Persona] = PERFECT) -> Env:
    """Create the run: seed the team (+ the pm agent), channel, board, meetings."""
    env = Env.make(SCENARIO, run_id, seed, force=force, root=root)
    seed_cast(env.store, cast=with_personas(CAST, member_persona))
    env.store.db.execute(
        "INSERT INTO channel (id, name, kind) VALUES (?, ?, 'channel')", (CHANNEL, CHANNEL))
    _seed_board(env)
    _schedule_meetings(env)
    env.store.db.backup_to(Env.seed_path(run_id, root))
    return env


if __name__ == "__main__":
    build()
    print(f"Built scenario {SCENARIO!r} at runs/{SCENARIO}/ (the saturated team week "
          "with a scripted PM closing and directing work over Slack).")
