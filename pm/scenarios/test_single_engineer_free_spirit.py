"""The "Single Engineer, Free Spirit" scenario — the overloaded solo board, unmanaged.

Alice alone carries the whole "Meeting Transcripts v1" push
(``pm/transcript/project_single_engineer.md``): six high-priority project tasks
totalling 2400 minutes — exactly her 40-hour, meeting-free week — plus five
low-priority backlog tickets (20 h) the week cannot also absorb. The board holds
60 h against 40 h of capacity, so triage is the game.

By default alice works as a :data:`~pm.npc.persona.FREE_SPIRIT` — picking at
random, ignoring priority — and no one intervenes. Backlog work displaces
project tasks, so part of the project is left over at Fri 17:00: the baseline a
steering PM would have to rescue. Re-seed with
``build(member_persona=PERFECT)`` to watch priority-ordered work ship the six
project tasks (and nothing else) by exactly Fri 17:00.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pm.env.environment import RUNS_DIR, Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.cast import CAST as _FULL_CAST
from pm.npc.cast import seed_cast, with_personas
from pm.npc.persona import FREE_SPIRIT, Persona
from pm.scenarios.project_board import PROJECT_ID as PROJECT_ID
from pm.scenarios.project_board import PROJECT_NAME as PROJECT_NAME
from pm.sim.clock import WEEK_END_TICK
from pm.transcript import project_tasks
from pm.world.models import Project

SCENARIO = "test_single_engineer_free_spirit"

CAST = [c for c in _FULL_CAST if c.id == "alice"]
MEMBERS = [c.id for c in CAST]

HIGH_PRIORITY, LOW_PRIORITY = 1, 3

# The project: the six tasks from project_single_engineer.md, 2400 min = the
# whole 40-h week. Every one must ship — they are the high-priority set.
HIGH: list[tuple[str, int]] = [
    (t["title"], int(t["estimate_minutes"]))
    for t in project_tasks(board="single_engineer")
]

# Backlog: nice-to-have, off-project. 5 x 240 min = 20 h; total board 60 h.
LOW: list[tuple[str, int]] = [
    ("Refactor ingest retry helpers", 240),
    ("Add tracing spans to search path", 240),
    ("Migrate lint config to shared preset", 240),
    ("Backfill API docs for v1 endpoints", 240),
    ("Prototype websocket compression", 240),
]


def _seed_board(env: Env) -> None:
    """1 epic, the project story and a backlog story, 11 tasks for alice."""
    env.store.add_project(Project(
        id=PROJECT_ID, name=PROJECT_NAME, deadline_tick=WEEK_END_TICK))
    repo = JiraRepository(env.store)
    repo.ensure_schema()
    api = JiraApi(repo, env.engine)

    epic = api.create_issue(PROJECT_ID, "epic", "Transcripts launch week", actor="alice")
    for story_title, rows, priority in (
        (PROJECT_NAME, HIGH, HIGH_PRIORITY),
        ("Backlog", LOW, LOW_PRIORITY),
    ):
        story = api.create_issue(
            PROJECT_ID, "story", story_title, parent=epic.id, actor="alice")
        for title, minutes in rows:
            api.create_issue(
                PROJECT_ID, "task", title, parent=story.id, estimate_minutes=minutes,
                assignee="alice", component="backend", priority=priority,
                actor="alice")


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
