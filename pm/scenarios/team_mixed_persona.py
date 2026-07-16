"""The "Team, Mixed Personas" scenario — the saturated team week, unmanaged, mixed personas.

Same board + meeting fabric as :mod:`pm.scenarios.team_with_jira` (66 tasks tiling five
members' meeting-free calendars), but bob and elieen work as
:data:`~pm.npc.persona.FREE_SPIRIT` and clare as :data:`~pm.npc.persona.HEADS_DOWN`
(alice and david stay perfect). With no PM the mix cannot finish the zero-slack week —
the baseline a steering PM would have to rescue.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pm.env.environment import RUNS_DIR, Env
from pm.npc.cast import seed_cast, with_personas
from pm.npc.persona import FREE_SPIRIT, HEADS_DOWN, Persona
from pm.scenarios.team_with_jira import CAST as CAST
from pm.scenarios.team_with_jira import MEMBERS as MEMBERS
from pm.scenarios.team_with_jira import PROJECT_ID as PROJECT_ID
from pm.scenarios.team_with_jira import _schedule_meetings, _seed_board

SCENARIO = "team_mixed_persona"

# The mix that misses the saturated week unmanaged.
MIXED: dict[str, Persona] = {
    "bob": FREE_SPIRIT, "clare": HEADS_DOWN, "elieen": FREE_SPIRIT,
}


def build(run_id: str = SCENARIO, *, seed: int = 42, root: Path = RUNS_DIR,
          force: bool = True,
          member_persona: Persona | Mapping[str, Persona] = MIXED) -> Env:
    """Create the run: seed the mixed-persona team + board + meetings, snapshot."""
    env = Env.make(SCENARIO, run_id, seed, force=force, root=root)
    seed_cast(env.store, cast=with_personas(CAST, member_persona))
    _seed_board(env)
    _schedule_meetings(env)
    env.store.db.backup_to(Env.seed_path(run_id, root))
    return env


if __name__ == "__main__":
    build()
    print(f"Built scenario {SCENARIO!r} at runs/{SCENARIO}/ (mixed personas, unmanaged "
          "— the saturated week misses).")
