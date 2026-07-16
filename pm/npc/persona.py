"""Behavioral personas — how a coworker works the board, not just who they are.

A :class:`Persona` is a small set of composable trait flags read back by the
NPC component (:mod:`pm.sim.npc` — the ``WorkDriver`` and the reactions)
to shape *what* an NPC does. It is folded into ``Person.persona`` JSON by
:func:`pm.npc.cast.seed_cast` (under the ``"behavior"`` key), so it lives in
SQLite and replays deterministically like everything else.

There are three named presets:

* :data:`PERFECT` — the competent worker: evaluates dependencies, works by
  priority, finishes on time, and posts a Slack status update when picking up
  work (keeping the team unblocked and informed).
* :data:`HEADS_DOWN` — heads-down builder: ships the work, but only updates the
  board when the team syncs — a standup or a Slack mention prompts the close.
* :data:`FREE_SPIRIT` — follows their own path through the backlog: picks the
  next ticket at (seeded) random and doesn't wait on dependency order, so
  blocked/unready tickets get worked too.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pm.world.models import Person

WorkStyle = Literal["by_priority", "freestyle"]
BoardUpdates = Literal["on_finish", "when_asked"]


@dataclass(frozen=True)
class Persona:
    """Composable behavior traits for a coworker NPC.

    * ``work_style`` — how the next ticket is chosen. ``by_priority`` takes the
      highest-priority ready ticket; ``freestyle`` picks at (seeded) random from
      everything on their plate, blocked tickets included.
    * ``board_updates`` — ``on_finish`` marks work ``done`` as it completes;
      ``when_asked`` leaves it in ``in_review`` until a standup ends or a
      Slack message names the person.
    * ``announces_progress`` — when ``True`` the NPC posts a Slack status update
      as it picks up work (raising the team's visibility). Requires a
      ``status_channel`` wired into the ``WorkDriver`` (:mod:`pm.sim.npc`);
      a no-op without one.
    """

    work_style: WorkStyle = "by_priority"
    board_updates: BoardUpdates = "on_finish"
    announces_progress: bool = False


PERFECT = Persona(announces_progress=True)
HEADS_DOWN = Persona(board_updates="when_asked")
FREE_SPIRIT = Persona("freestyle")

PRESETS: dict[str, Persona] = {
    "perfect": PERFECT,
    "heads_down": HEADS_DOWN,
    "free_spirit": FREE_SPIRIT,
}


def to_dict(persona: Persona) -> dict[str, object]:
    """Serialize a persona for storage in the ``Person.persona`` JSON."""
    return asdict(persona)


def from_person(person: "Person | None") -> Persona:
    """Reconstruct a persona from a ``Person`` row, falling back to ``PERFECT``.

    Only known fields are read, so extra/missing keys in stored JSON are tolerated.
    """
    if person is None:
        return PERFECT
    behavior = person.persona.get("behavior", {})
    known = {f.name for f in fields(Persona)}
    return Persona(**{k: v for k, v in behavior.items() if k in known})
