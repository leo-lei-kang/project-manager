"""The "Single Engineer, Free Spirit" scenario — the overloaded solo board, unmanaged.

Same board as :mod:`pm.scenarios.test_single_engineer` (the six 40-h transcripts
project tasks plus 20 h of backlog), but alice works as a
:data:`~pm.npc.persona.FREE_SPIRIT` — picking at random, ignoring priority — and no one
intervenes. Backlog work displaces project tasks, so part of the project is left
over at Fri 17:00: the baseline a steering PM would have to rescue.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pm.env.environment import RUNS_DIR, Env
from pm.npc.cast import CAST as _FULL_CAST
from pm.npc.cast import seed_cast, with_personas
from pm.npc.persona import FREE_SPIRIT, Persona
from pm.scenarios.test_single_engineer import PROJECT_ID as PROJECT_ID
from pm.scenarios.test_single_engineer import _seed_board

SCENARIO = "test_single_engineer_free_spirit"

CAST = [c for c in _FULL_CAST if c.id == "alice"]
MEMBERS = [c.id for c in CAST]


def build(run_id: str = SCENARIO, *, seed: int = 42, root: Path = RUNS_DIR,
          force: bool = True,
          member_persona: Persona | Mapping[str, Persona] = FREE_SPIRIT) -> Env:
    """Create the run: seed alice (free spirit) + the overloaded board, snapshot."""
    env = Env.make(SCENARIO, run_id, seed, force=force, root=root)
    seed_cast(env.store, cast=with_personas(CAST, member_persona))
    _seed_board(env)
    env.store.db.backup_to(Env.seed_path(run_id, root))
    return env


if __name__ == "__main__":
    build()
    print(f"Built scenario {SCENARIO!r} at runs/{SCENARIO}/ (a free-spirit engineer, "
          "unmanaged — backlog picks leave part of the project over).")
