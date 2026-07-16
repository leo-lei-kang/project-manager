"""The team_no_jira scenario: work tracked in meeting notes only.

project_team.md is the single source: the Monday kickoff seeds the informal ``task``
rows from it (and embeds it in the transcript), and later meetings update statuses
— nothing ever reaches the Jira board.
"""

from __future__ import annotations

from pm.agent.tools import AgentTools
from pm.jira.repository import JiraRepository
from pm.scenarios import runner, team_no_jira
from pm.transcript import project_brief, project_tasks

BREAKDOWN = {t["id"]: t for t in project_tasks()}


def test_project_md_breakdown_parses():
    assert len(BREAKDOWN) == 25
    assert all(t["id"].startswith("NOTES-") for t in BREAKDOWN.values())
    assert {t["dri_id"] for t in BREAKDOWN.values()} == {"alice", "bob", "clare", "david", "elieen"}
    assert all(t["status"] == "todo" for t in BREAKDOWN.values())
    assert all(int(t["estimate_minutes"]) > 0 for t in BREAKDOWN.values())


def test_kickoff_creates_tasks_but_no_issues(tmp_path):
    env = team_no_jira.build(run_id="nj", root=tmp_path)
    assert env.store.list_tasks() == []  # nothing until the kickoff ends

    env.engine.advance(180)  # Mon 12:00 — the kickoff (11:00-12:00) just ended
    tasks = {t.id: t for t in env.store.list_tasks()}
    assert set(tasks) == set(BREAKDOWN)
    assert all(t.dri_id == BREAKDOWN[t.id]["dri_id"] for t in tasks.values())
    assert all(t.status == "todo" for t in tasks.values())
    assert all(t.source_meeting_id == "no-jira-0" for t in tasks.values())
    # ...while the Jira board has never seen a ticket
    assert JiraRepository(env.store).list_issues() == []

    monday = env.store.list_transcripts(available_by=env.clock.now())[0]
    assert project_brief() in monday.body  # the kickoff embeds the project doc
    env.close()


def test_week_updates_statuses_in_notes_only(tmp_path):
    env = team_no_jira.build(run_id="nj-week", root=tmp_path)
    runner.drive(env, team_no_jira)

    # Fri's standup leaves each member's queue: 3 done, 1 mid-flight, 1 untouched.
    done = {"NOTES-1", "NOTES-2", "NOTES-3", "NOTES-4", "NOTES-6",
            "NOTES-7", "NOTES-5", "NOTES-14", "NOTES-18", "NOTES-22",
            "NOTES-8", "NOTES-11", "NOTES-15", "NOTES-19", "NOTES-23"}
    in_progress = {"NOTES-9", "NOTES-12", "NOTES-16", "NOTES-20", "NOTES-24"}
    status = {t.id: t.status for t in env.store.list_tasks()}
    assert status == {
        t: "done" if t in done else "in_progress" if t in in_progress else "todo"
        for t in BREAKDOWN}
    assert JiraRepository(env.store).list_issues() == []  # still nothing filed

    seen = AgentTools(env).read_transcripts()
    assert len(seen) == 5
    assert any("reconstruct the week" in t["body"] for t in seen)  # Friday's ask
    env.close()


