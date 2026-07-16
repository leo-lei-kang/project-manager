"""Seed the "Meeting Transcripts v1" project from ``pm/transcript/project_team.md``.

The shared builder behind the ``team_*`` scenarios: the project doc is the
single source of the task breakdown, and ``jira_ids`` selects which of those
tasks actually get filed as Jira tickets (assignee = the task's DRI). The full
breakdown reaches the informal ``task`` table through the Monday kickoff
meeting's payload regardless — so a scenario controls exactly how much of the
week's work is visible on the board versus only in the meeting notes.
"""

from __future__ import annotations

from collections.abc import Collection

from pm.env.environment import Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.cast import MEMBERS as _CAST_MEMBERS
from pm.sim.clock import WEEK_END_TICK
from pm.transcript import project_tasks
from pm.world.models import Project

PROJECT_ID = "MT"
PROJECT_NAME = "Meeting Transcripts v1"

BACKLOG_PRIORITY = 3

# Off-project, nice-to-have backlog: two 4-hour tickets per member, flavored to
# their discipline. Low priority — never the top of anyone's queue.
_BACKLOG: list[tuple[str, str]] = [
    ("alice", "Refactor ingest retry helpers"),
    ("alice", "Add tracing spans to search path"),
    ("bob", "Benchmark alternative STT vendors"),
    ("bob", "Clean up training-data scripts"),
    ("clare", "Prune unused Storybook stories"),
    ("clare", "Audit frontend bundle size"),
    ("david", "Backfill API docs for v1 endpoints"),
    ("david", "Migrate lint config to shared preset"),
    ("elieen", "Refresh the shared icon set"),
    ("elieen", "Audit spacing tokens across screens"),
]


def seed_project_board(env: Env, *, jira_ids: Collection[str] = ()) -> None:
    """Add the project row and file Jira tickets for the selected task ids only.

    ``jira_ids`` are ``project_team.md`` task ids (``NOTES-*``). An empty selection
    leaves the board bare (the ``issue`` table still exists — ``pm eval`` needs
    it) while the meeting notes carry the whole plan.
    """
    env.store.add_project(Project(
        id=PROJECT_ID, name=PROJECT_NAME, deadline_tick=WEEK_END_TICK))
    repo = JiraRepository(env.store)
    repo.ensure_schema()
    api = JiraApi(repo, env.engine)

    by_id = {t["id"]: t for t in project_tasks()}
    unknown = set(jira_ids) - set(by_id)
    if unknown:
        raise ValueError(f"unknown project_team.md task id(s): {sorted(unknown)}")
    disciplines = {c.id: c.discipline for c in _CAST_MEMBERS}
    for task_id in sorted(jira_ids):
        t = by_id[task_id]
        api.create_issue(
            PROJECT_ID, "task", t["title"], estimate_minutes=int(t["estimate_minutes"]),
            assignee=t["dri_id"], component=disciplines[t["dri_id"]], actor="alice")


def seed_backlog_epic(env: Env) -> None:
    """File the low-priority "Engineering backlog" epic: ten 4-h tickets, two per
    member — the visible-but-off-project work a busy board is made of."""
    repo = JiraRepository(env.store)
    repo.ensure_schema()
    api = JiraApi(repo, env.engine)
    disciplines = {c.id: c.discipline for c in _CAST_MEMBERS}

    epic = api.create_issue(PROJECT_ID, "epic", "Engineering backlog",
                            priority=BACKLOG_PRIORITY, actor="alice")
    story = api.create_issue(PROJECT_ID, "story", "Backlog", parent=epic.id,
                             priority=BACKLOG_PRIORITY, actor="alice")
    for assignee, title in _BACKLOG:
        api.create_issue(
            PROJECT_ID, "task", title, parent=story.id, estimate_minutes=240,
            assignee=assignee, component=disciplines[assignee],
            priority=BACKLOG_PRIORITY, actor="alice")
