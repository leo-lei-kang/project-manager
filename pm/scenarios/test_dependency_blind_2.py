"""The "Dependency Blind 2" scenario — two engineers, no meetings, zero slack.

Alice (backend) and Clare (frontend) each carry exactly 40 hours of work: eight
300-minute tasks tiling the 2400-tick week. Half of each engineer's tasks depend
on the other's — every odd task blocks the partner's next even task, a
just-in-time handoff at each 300-tick boundary (the blocker's intended
completion == the dependent's intended start). Total work equals total capacity,
so only working the board in dependency + priority order finishes the week.

``build(member_persona=...)`` seeds both engineers with the given behavior
persona (default: the standard priority/dependency-respecting one, which
finishes at exactly Fri 17:00). Re-seed with :data:`pm.npc.persona.CHAOTIC` to
watch an engineer strand their partner idle behind a deferred blocker — idle
time the week cannot absorb — or :data:`pm.npc.persona.DEPENDENCY_BLIND` to
watch blocked tickets get worked before the work they depend on.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from pm.env.environment import RUNS_DIR, Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.cast import CAST as _FULL_CAST
from pm.npc.cast import seed_cast
from pm.npc.persona import DEFAULT, Persona
from pm.sim.clock import WEEK_END_TICK
from pm.world.models import Project

SCENARIO = "test_dependency_blind_2"
PROJECT_ID = "CAP"

CAST = [c for c in _FULL_CAST if c.id in ("alice", "clare")]  # backend + frontend
MEMBERS = [c.id for c in CAST]

# Eight tasks x 300 minutes == the full 2400-tick work week, per engineer.
# priority == the 1-based ordinal; the intended schedule is ordinal order, task i
# spanning ticks [(i-1)*300, i*300).
TASKS: dict[str, list[tuple[str, int]]] = {
    "alice": [  # backend
        ("Transcript ingest API", 300),
        ("Wire caption edits into store", 300),
        ("Speaker labels endpoint", 300),
        ("Persist caption style prefs", 300),
        ("Transcript search endpoint", 300),
        ("Share-link permissions API", 300),
        ("Export service (SRT/PDF)", 300),
        ("Wire viewer analytics into pipeline", 300),
    ],
    "clare": [  # frontend
        ("Caption editor UI", 300),
        ("Render live transcript stream", 300),
        ("Caption style settings panel", 300),
        ("Speaker label badges", 300),
        ("Share dialog UI", 300),
        ("Transcript search UI", 300),
        ("Viewer analytics events", 300),
        ("Export menu UI", 300),
    ],
}

# Dependency edges: ((blocker_member, ordinal), (dependent_member, ordinal)).
# Every odd task blocks the *other* engineer's next even task, so half of each
# engineer's tasks depend on the partner. Each handoff is just-in-time: the
# blocker's intended completion tick equals the dependent's intended start tick.
DEPS: list[tuple[tuple[str, int], tuple[str, int]]] = [
    edge
    for i in (1, 3, 5, 7)
    for edge in ((("alice", i), ("clare", i + 1)), (("clare", i), ("alice", i + 1)))
]

_STORIES: dict[str, str] = {
    "alice": "Backend services",
    "clare": "Frontend experience",
}


def _seed_board(env: Env) -> None:
    """1 epic, 2 per-engineer stories, 16 tasks tiling the week, then the dep edges."""
    env.store.add_project(Project(
        id=PROJECT_ID, name="Transcript Workspace", deadline_tick=WEEK_END_TICK))
    repo = JiraRepository(env.store)
    repo.ensure_schema()
    api = JiraApi(repo, env.engine)

    epic = api.create_issue(PROJECT_ID, "epic", "Transcript workspace week", actor="alice")
    disciplines = {c.id: c.discipline for c in CAST}
    keys: dict[tuple[str, int], str] = {}
    for member, rows in TASKS.items():
        story = api.create_issue(
            PROJECT_ID, "story", _STORIES[member], parent=epic.id, actor="alice")
        for ordinal, (title, minutes) in enumerate(rows, start=1):
            issue = api.create_issue(
                PROJECT_ID, "task", title, parent=story.id, estimate_minutes=minutes,
                assignee=member, component=disciplines[member], priority=ordinal,
                actor="alice")
            keys[(member, ordinal)] = issue.id

    # Link after all tasks exist: each member's blockers interleave the other's.
    for blocker, dependent in DEPS:
        api.link_issue(keys[dependent], keys[blocker], actor="alice")


def build(run_id: str = SCENARIO, *, seed: int = 42, root: Path = RUNS_DIR,
          force: bool = True, member_persona: Persona = DEFAULT) -> Env:
    """Create the run, seed the two engineers + cross-blocked board, snapshot ``seed.db``."""
    env = Env.make(SCENARIO, run_id, seed, force=force, root=root)
    cast = [replace(c, persona=member_persona) for c in CAST]
    seed_cast(env.store, cast=cast)
    _seed_board(env)
    env.store.db.backup_to(Env.seed_path(run_id, root))
    return env


if __name__ == "__main__":
    build()
    print(f"Built scenario {SCENARIO!r} at runs/{SCENARIO}/ (two engineers, 16 "
          "cross-blocked tasks; in dependency + priority order the board finishes "
          "at Fri 17:00 sharp).")
