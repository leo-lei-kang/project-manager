"""The "Single Engineer, Free Spirit" scenario — the overloaded solo board, unmanaged.

Alice alone carries the whole "Meeting Transcripts v1" push
(``pm/transcript/project_single_engineer.md``), split into two epics: a
high-priority one with eight 3-5 h project tasks totalling 1740 minutes (29 h)
— shippable in full even around the daily 30-minute standups and a 4-hour OOO
on Tuesday morning (33.5 h of working time) — and a low-priority backlog epic
with six tickets (23 h) the week cannot also absorb. The board holds 52 h
against 33.5 h of capacity, so triage is the game.

By default alice works as a :data:`~pm.npc.persona.FREE_SPIRIT` — picking at
random, ignoring priority — and no one intervenes. Backlog work displaces
project tasks, so part of the project is left over at Fri 17:00: the baseline a
steering PM would have to rescue. Re-seed with
``build(member_persona=PERFECT)`` to watch priority-ordered work ship all
eight project tasks and still close one backlog ticket.
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
from pm.sim.clock import MINUTES_PER_WORKDAY, WEEK_END_TICK, WORKDAYS
from pm.sim.events import MeetingEvent, OOOEvent
from pm.transcript import project_tasks
from pm.world.models import Project

SCENARIO = "single_engineer"

CAST = [c for c in _FULL_CAST if c.id == "alice"]
MEMBERS = [c.id for c in CAST]

HIGH_PRIORITY, LOW_PRIORITY = 1, 3

# The project: the eight tasks from project_single_engineer.md (3-5 h each),
# 1740 min = 29 h. Every one must ship — they are the high-priority set, and
# they fit the week easily even with the daily standups (40 h - 2.5 h of meetings).
HIGH: list[tuple[str, int]] = [
    (t["title"], int(t["estimate_minutes"]))
    for t in project_tasks(board="single_engineer")
]

# Backlog: nice-to-have, off-project (export/share moved down from the project
# scope). 5 x 240 + 180 = 1380 min = 23 h; total board 52 h.
LOW: list[tuple[str, int]] = [
    ("Refactor ingest retry helpers", 240),
    ("Add tracing spans to search path", 240),
    ("Migrate lint config to shared preset", 240),
    ("Backfill API docs for v1 endpoints", 240),
    ("Prototype websocket compression", 240),
    ("Transcript export and share links", 180),
]


def _seed_board(env: Env) -> None:
    """Two epics — the high-priority project (fits the week) and the low-priority
    backlog — one story each, 14 tasks for alice; plus the daily standups."""
    env.store.add_project(Project(
        id=PROJECT_ID, name=PROJECT_NAME, deadline_tick=WEEK_END_TICK))
    repo = JiraRepository(env.store)
    repo.ensure_schema()
    api = JiraApi(repo, env.engine)

    for epic_title, story_title, rows, priority in (
        ("Transcripts launch week", PROJECT_NAME, HIGH, HIGH_PRIORITY),
        ("Engineering backlog", "Backlog", LOW, LOW_PRIORITY),
    ):
        epic = api.create_issue(PROJECT_ID, "epic", epic_title, priority=priority,
                                actor="alice")
        story = api.create_issue(PROJECT_ID, "story", story_title, parent=epic.id,
                                 priority=priority, actor="alice")
        for title, minutes in rows:
            api.create_issue(
                PROJECT_ID, "task", title, parent=story.id, estimate_minutes=minutes,
                assignee="alice", component="backend", priority=priority,
                actor="alice")

    # A 30-minute standup at 09:00 every day (2.5 h of the 40-h week).
    for day in range(WORKDAYS):
        env.engine.schedule(MeetingEvent(
            owner_id="alice", start_tick=day * MINUTES_PER_WORKDAY, duration=30,
            payload={"meeting_id": f"standup-{day}", "kind": "standup",
                     "title": "Daily standup", "attendees": MEMBERS}))

    # Alice is out Tuesday 09:00-13:00 (4 h); that day's standup is skipped.
    env.engine.schedule(OOOEvent(
        owner_id="alice", start_tick=MINUTES_PER_WORKDAY, duration=240,
        payload={"reason": "appointment"}))


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
