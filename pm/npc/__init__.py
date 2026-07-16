"""Stateful coworkers (NPCs).

Each coworker has a role, a persona, and its own goals/constraints. NPC activity
is purely event-driven: coworker actions are *scheduled events*
(:mod:`pm.sim.scheduler`) that fire only as sim-time advances — never a real
background thread. Response latency comes from each person's seeded
``delay_min``/``delay_max`` window, so a scenario replays identically.

- :mod:`pm.npc.cast` — the consolidated cast (members + stakeholders + PM agent).
- :mod:`pm.npc.behavior` — board-pickup scheduling hooks.
- :mod:`pm.npc.reactions` — reactive per-event-type hooks.
"""

from __future__ import annotations

from pm.npc.cast import AGENT, CAST, MEMBERS, STAKEHOLDERS, CastMember, seed_cast

__all__ = ["CAST", "MEMBERS", "STAKEHOLDERS", "AGENT", "CastMember", "seed_cast"]
