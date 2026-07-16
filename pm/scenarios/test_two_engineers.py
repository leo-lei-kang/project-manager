"""The "Two Engineers" scenario — two engineers, no meetings, zero slack.

Alice (backend) and Clare (frontend) split the "Meeting Transcripts v1" project
(``pm/transcript/project_two_engineers.md``), each carrying exactly 40 hours: eight
300-minute tasks tiling the 2400-tick week. Half of each engineer's tasks depend
on the other's — every odd task blocks the partner's next even task, a
just-in-time handoff at each 300-tick boundary (the blocker's intended
completion == the dependent's intended start). Total work equals total capacity,
so only working the board in dependency + priority order finishes the week.

``build(member_persona=...)`` seeds both engineers with the given behavior
persona (default: :data:`pm.npc.persona.PERFECT`, which finishes at exactly Fri
17:00). Re-seed with :data:`pm.npc.persona.FREE_SPIRIT` to watch engineers work
blocked tickets before the work they depend on and strand each other idle behind
deferred blockers — idle time the week cannot absorb.

    uv run python -m pm.scenarios.test_two_engineers [persona]

builds ``runs/test_two_engineers/`` with the named persona preset
(:data:`pm.npc.persona.PRESETS`; default ``perfect``).
"""

from __future__ import annotations

from collections.abc import Mapping

from pathlib import Path

from pm.env.environment import RUNS_DIR, Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.cast import CAST as _FULL_CAST
from pm.npc.cast import seed_cast, with_personas
from pm.npc.persona import PERFECT, PRESETS, Persona
from pm.scenarios.project_board import PROJECT_ID as PROJECT_ID
from pm.scenarios.project_board import PROJECT_NAME as PROJECT_NAME
from pm.sim.clock import WEEK_END_TICK
from pm.transcript import project_tasks
from pm.world.models import Project

SCENARIO = "test_two_engineers"

CAST = [c for c in _FULL_CAST if c.id in ("alice", "clare")]  # backend + frontend
MEMBERS = [c.id for c in CAST]

# Eight tasks x 300 minutes == the full 2400-tick work week, per engineer — the
# breakdown from project_two_engineers.md, in table order per member.
# priority == the 1-based ordinal; the intended schedule is ordinal order, task i
# spanning ticks [(i-1)*300, i*300).
TASKS: dict[str, list[tuple[str, int]]] = {}
for _row in project_tasks(board="two_engineers"):
    TASKS.setdefault(_row["dri_id"], []).append(
        (_row["title"], int(_row["estimate_minutes"])))

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
        id=PROJECT_ID, name=PROJECT_NAME, deadline_tick=WEEK_END_TICK))
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
          force: bool = True, member_persona: Persona | Mapping[str, Persona] = PERFECT) -> Env:
    """Create the run, seed the two engineers + cross-blocked board, snapshot ``seed.db``."""
    env = Env.make(SCENARIO, run_id, seed, force=force, root=root)
    seed_cast(env.store, cast=with_personas(CAST, member_persona))
    _seed_board(env)
    env.store.db.backup_to(Env.seed_path(run_id, root))
    return env


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "perfect"
    if name not in PRESETS:
        sys.exit(f"unknown persona {name!r} (choices: {', '.join(PRESETS)})")
    build(member_persona=PRESETS[name])
    print(f"Built scenario {SCENARIO!r} at runs/{SCENARIO}/ with persona {name!r} "
          "(two engineers, 16 cross-blocked tasks; in dependency + priority order "
          "the board finishes at Fri 17:00 sharp).")
