"""Consolidated cast — team members, stakeholders, and the PM agent.

The single source of truth for the people in a scenario, with an explicit
member / stakeholder / agent distinction. ``seed_cast`` writes them as ``Person``
rows (discipline / works / kind folded into the persona JSON so behavior and the
evaluator can read them back).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pm.db.store import Store
from pm.npc.persona import DEFAULT, Persona, to_dict
from pm.world.models import Person

Kind = Literal["member", "stakeholder", "agent"]


@dataclass(frozen=True)
class CastMember:
    id: str
    name: str
    role: str
    discipline: str
    kind: Kind
    works: bool  # ICs work issues; stakeholders/agent create/assign/manage
    delay_min: int = 15
    delay_max: int = 90
    goals: tuple[str, ...] = ()
    priorities: tuple[str, ...] = ()
    tone: str = ""
    persona: Persona = DEFAULT  # how they work the board (see pm.npc.persona)


CAST: list[CastMember] = [
    # -- team members (implementers) -----------------------------------------
    CastMember("alice", "Alice", "Backend Engineer", "backend", "member", True,
               15, 90, ("keep the API and data model sound",),
               ("unblock dependents early", "pay down risk"), "terse, pragmatic"),
    CastMember("bob", "Bob", "ML / Speech Engineer", "ml", "member", True,
               15, 90, ("accurate, low-latency transcription",),
               ("ship the STT pipeline", "measure quality"), "analytical"),
    CastMember("clare", "Clare", "Frontend Engineer", "frontend", "member", True,
               15, 90, ("a polished captions UI",),
               ("match the designs", "handle error states"), "friendly"),
    CastMember("david", "David", "Backend Engineer", "backend", "member", True,
               15, 90, ("reliable storage and APIs",),
               ("data-model soundness", "search performance"), "steady"),
    CastMember("elieen", "Elieen", "Product Designer", "design", "member", True,
               30, 120, ("a clear, accessible experience",),
               ("specs before build", "visual consistency"), "thoughtful"),
    # -- stakeholders (create / assign / apply pressure) ---------------------
    CastMember("erin", "Erin Malik", "Engineering Manager", "management", "stakeholder", False,
               20, 60, ("the team hits the launch",),
               ("clear blockers", "balance load"), "supportive, decisive"),
    CastMember("vera", "Vera Diaz", "VP Product", "product", "stakeholder", False,
               45, 180, ("launch on schedule",),
               ("scope discipline", "customer impact"), "direct"),
    CastMember("xavier", "Xavier Cole", "Chief Technology Officer", "exec", "stakeholder", False,
               60, 240, ("launch on time",),
               ("business impact", "risk visibility"), "high-level, impatient"),
    # -- the agent under test ------------------------------------------------
    CastMember("pm", "PM", "Project Manager", "management", "agent", False,
               0, 0, ("keep the project moving",),
               ("unblock", "prioritize", "communicate"), "organized"),
]

MEMBERS: list[CastMember] = [c for c in CAST if c.kind == "member"]
STAKEHOLDERS: list[CastMember] = [c for c in CAST if c.kind == "stakeholder"]
AGENT: CastMember = next(c for c in CAST if c.kind == "agent")


def seed_cast(store: Store, cast: list[CastMember] = CAST) -> list[Person]:
    """Insert the cast as ``person`` rows; return the created people.

    ``discipline`` / ``kind`` / ``works`` are persisted inside the persona JSON.
    """
    people: list[Person] = []
    for c in cast:
        persona = {
            "discipline": c.discipline,
            "kind": c.kind,
            "works": c.works,
            "goals": list(c.goals),
            "priorities": list(c.priorities),
            "tone": c.tone,
            "behavior": to_dict(c.persona),
        }
        person = Person(
            id=c.id, name=c.name, role=c.role, is_agent=(c.kind == "agent"),
            persona=persona, delay_min=c.delay_min, delay_max=c.delay_max,
        )
        store.add_person(person)
        people.append(person)
    return people
