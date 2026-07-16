"""Behavioral personas — how a coworker works the board, not just who they are.

A :class:`Persona` is a small set of composable trait flags read back by the
scheduling hooks (:mod:`pm.npc.behavior`) and the reactions (:mod:`pm.npc.reactions`)
to shape *what* an NPC does. It is folded into ``Person.persona`` JSON by
:func:`pm.npc.cast.seed_cast` (under the ``"behavior"`` key), so it lives in
SQLite and replays deterministically like everything else.

The dataclass defaults reproduce today's behavior exactly (priority order,
dependencies respected, no blocker weighting, auto-close), so an NPC with no
explicit persona behaves as it always has. The named presets opt into the
imperfect — and one perfect — variants.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pm.world.models import Person

TaskSelection = Literal["priority", "random"]
Completion = Literal["auto", "on_reminder"]


@dataclass(frozen=True)
class Persona:
    """Composable behavior traits for a coworker NPC.

    * ``task_selection`` — ``priority`` takes the highest-priority ready issue;
      ``random`` ignores priority and picks a candidate at (seeded) random.
    * ``respects_dependencies`` — when ``False`` the NPC also picks up ``blocked``
      issues (works things that are not actually ready).
    * ``prioritizes_blockers`` — when ``True`` the NPC prefers issues that block
      the most other work (the critical path), then priority.
    * ``completion`` — ``auto`` marks work ``done`` on finish; ``on_reminder``
      leaves it in ``in_review`` until a standup completes or a Slack message
      names the person.
    """

    task_selection: TaskSelection = "priority"
    respects_dependencies: bool = True
    prioritizes_blockers: bool = False
    completion: Completion = "auto"


DEFAULT = Persona()
PERFECT = Persona(prioritizes_blockers=True)
FORGETFUL_CLOSER = Persona(completion="on_reminder")
CHAOTIC = Persona(task_selection="random")
DEPENDENCY_BLIND = Persona(task_selection="random", respects_dependencies=False)

PRESETS: dict[str, Persona] = {
    "default": DEFAULT,
    "perfect": PERFECT,
    "forgetful_closer": FORGETFUL_CLOSER,
    "chaotic": CHAOTIC,
    "dependency_blind": DEPENDENCY_BLIND,
}


def to_dict(persona: Persona) -> dict[str, object]:
    """Serialize a persona for storage in the ``Person.persona`` JSON."""
    return asdict(persona)


def from_person(person: "Person | None") -> Persona:
    """Reconstruct a persona from a ``Person`` row, falling back to ``DEFAULT``.

    Only known fields are read, so extra/missing keys in stored JSON are tolerated.
    """
    if person is None:
        return DEFAULT
    behavior = person.persona.get("behavior", {})
    known = {f.name for f in fields(Persona)}
    return Persona(**{k: v for k, v in behavior.items() if k in known})
