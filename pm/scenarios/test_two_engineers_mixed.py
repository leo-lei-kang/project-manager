"""The "Two Engineers, Mixed" scenario — the cross-blocked pair, unmanaged, mixed personas.

Same zero-slack board as :mod:`pm.scenarios.test_two_engineers`, but alice works as a
:data:`~pm.npc.persona.FREE_SPIRIT` and clare as :data:`~pm.npc.persona.HEADS_DOWN`.
Clare's finished work sits in ``in_review`` and alice ignores dependency/priority order,
so the just-in-time cross handoffs stall and the week cannot finish unmanaged — the
baseline a steering PM would have to rescue.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pm.env.environment import RUNS_DIR, Env
from pm.npc.cast import CAST as _FULL_CAST
from pm.npc.cast import seed_cast, with_personas
from pm.npc.persona import FREE_SPIRIT, HEADS_DOWN, Persona
from pm.scenarios.test_two_engineers import PROJECT_ID as PROJECT_ID
from pm.scenarios.test_two_engineers import _seed_board

SCENARIO = "test_two_engineers_mixed"

CAST = [c for c in _FULL_CAST if c.id in ("alice", "clare")]
MEMBERS = [c.id for c in CAST]

# The mix that misses the week unmanaged.
MIXED: dict[str, Persona] = {"alice": FREE_SPIRIT, "clare": HEADS_DOWN}


def build(run_id: str = SCENARIO, *, seed: int = 42, root: Path = RUNS_DIR,
          force: bool = True,
          member_persona: Persona | Mapping[str, Persona] = MIXED) -> Env:
    """Create the run: seed the mixed-persona engineers + cross-blocked board, snapshot."""
    env = Env.make(SCENARIO, run_id, seed, force=force, root=root)
    seed_cast(env.store, cast=with_personas(CAST, member_persona))
    _seed_board(env)
    env.store.db.backup_to(Env.seed_path(run_id, root))
    return env


if __name__ == "__main__":
    build()
    print(f"Built scenario {SCENARIO!r} at runs/{SCENARIO}/ (mixed personas, unmanaged "
          "— the cross handoffs stall and the week misses).")
