"""The "Single Engineer" scenario — one engineer, 60 hours of tickets, 40-hour week.

Alice alone holds 3600 minutes (60 h) of Jira tickets against a 2400-tick
(40 h) work week with no meetings: seven high-priority launch blockers
(priority 1, 35 h) that must ship this week, and five low-priority backlog
items (priority 3, 25 h). The board cannot be finished — triage is the whole
game. Every task is 300 minutes, so any run completes exactly eight tasks; the
persona decides *which* eight:

* :data:`pm.npc.persona.PERFECT` (priority-ordered) works all seven launch
  blockers first, then one backlog item — every high-priority ticket ships.
* :data:`pm.npc.persona.FREE_SPIRIT` draws uniformly from the ready pool, so
  some high-priority tickets are left over at Fri 17:00.

``build(member_persona=...)`` seeds alice with the given behavior persona
(default: :data:`pm.npc.persona.PERFECT`, which works in priority order).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from pm.env.environment import RUNS_DIR, Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.cast import CAST as _FULL_CAST
from pm.npc.cast import seed_cast
from pm.npc.persona import PERFECT, Persona
from pm.sim.clock import WEEK_END_TICK
from pm.world.models import Project

SCENARIO = "test_single_engineer"
PROJECT_ID = "SOLO"

CAST = [c for c in _FULL_CAST if c.id == "alice"]
MEMBERS = [c.id for c in CAST]

HIGH_PRIORITY, LOW_PRIORITY = 1, 3

# Launch blockers: must ship this week. 7 x 300 min = 35 h — fits the 40-h week.
HIGH: list[tuple[str, int]] = [
    ("Fix auth bypass on transcript export", 300),
    ("Patch PII leak in caption logs", 300),
    ("Restore replay after storage failover", 300),
    ("Unbreak SSO login for enterprise tenants", 300),
    ("Fix billing double-charge on plan change", 300),
    ("Stop transcript loss on reconnect", 300),
    ("Ship GDPR delete endpoint", 300),
]

# Backlog: nice-to-have. 5 x 300 min = 25 h; total board 60 h > the 40-h week.
LOW: list[tuple[str, int]] = [
    ("Refactor ingest retry helpers", 300),
    ("Add tracing spans to search path", 300),
    ("Migrate lint config to shared preset", 300),
    ("Backfill API docs for v1 endpoints", 300),
    ("Prototype websocket compression", 300),
]


def _seed_board(env: Env) -> None:
    """1 epic, a launch-blockers story and a backlog story, 12 tasks for alice."""
    env.store.add_project(Project(
        id=PROJECT_ID, name="Solo Launch Week", deadline_tick=WEEK_END_TICK))
    repo = JiraRepository(env.store)
    repo.ensure_schema()
    api = JiraApi(repo, env.engine)

    epic = api.create_issue(PROJECT_ID, "epic", "Overloaded launch week", actor="alice")
    for story_title, rows, priority in (
        ("Launch blockers", HIGH, HIGH_PRIORITY),
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
          force: bool = True, member_persona: Persona = PERFECT) -> Env:
    """Create the run, seed alice + the overloaded board, snapshot ``seed.db``."""
    env = Env.make(SCENARIO, run_id, seed, force=force, root=root)
    cast = [replace(c, persona=member_persona) for c in CAST]
    seed_cast(env.store, cast=cast)
    _seed_board(env)
    env.store.db.backup_to(Env.seed_path(run_id, root))
    return env


if __name__ == "__main__":
    build()
    print(f"Built scenario {SCENARIO!r} at runs/{SCENARIO}/ (one engineer, 60 h of "
          "tickets in a 40-h week; the persona decides which eight tasks ship).")
