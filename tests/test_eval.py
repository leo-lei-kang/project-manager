"""The evaluator: project-completion verdict, closed Jira hours, per-person breakdown."""

from __future__ import annotations

import pytest

from pm.env.environment import Env
from pm.eval import evaluate, format_report
from pm.exceptions import ConfigurationError
from pm.jira.api import JiraApi
from pm.jira.models import Issue
from pm.jira.repository import JiraRepository
from pm.npc.behavior import assignee_pickup_hook
from pm.npc.persona import FREE_SPIRIT
from pm.scenarios import runner, team_no_jira, team_partial_jira
from pm.scenarios.team_with_jira import MEMBERS, PROJECT_ID, build
from pm.sim.simulation import Simulation
from pm.world.models import Project

EXPECTED_COUNTS = {"alice": 16, "bob": 13, "clare": 13, "david": 12, "elieen": 12}


def _run(tmp_path, run_id, **kwargs):
    env = build(run_id=run_id, root=tmp_path, **kwargs)
    api = JiraApi(JiraRepository(env.store), env.engine)
    Simulation(env).run(on_tick=assignee_pickup_hook(api, MEMBERS, PROJECT_ID))
    return env


def test_board_project_done(tmp_path):
    env = _run(tmp_path, "eval-ok")
    report = evaluate(env.store)

    assert report.goal_accomplished
    assert report.source == "board"  # no informal tasks: the Jira board stands in
    assert report.project_id == PROJECT_ID
    assert report.done_tasks == report.total_tasks == 66
    assert report.closed_jira_minutes == report.total_jira_minutes == 10575  # 176.25h
    assert report.remaining == []
    assert {p.person_id: len(p.done) for p in report.people} == EXPECTED_COUNTS
    assert all(p.remaining == [] for p in report.people)

    text = format_report(report)
    assert "PROJECT DONE" in text and "NOT DONE" not in text
    assert "176.25h" in text and "none" in text
    env.close()


def test_board_project_unfinished(tmp_path):
    env = _run(tmp_path, "eval-chaos", member_persona=FREE_SPIRIT)
    report = evaluate(env.store)

    assert not report.goal_accomplished
    assert 0 < report.done_tasks < 66
    assert 0 < report.closed_jira_minutes < report.total_jira_minutes
    assert report.remaining  # unfinished tasks are reported
    # every remaining task is carried in exactly one person's remaining list
    carried = [t.id for p in report.people for t in p.remaining]
    assert sorted(carried) == sorted(t.id for t in report.remaining)
    assert "PROJECT NOT DONE" in format_report(report)
    env.close()


def test_notes_project_not_done_despite_empty_board(tmp_path):
    # The board says nothing is happening; the notes show the project at 4/6.
    env = team_no_jira.build(run_id="eval-nj", root=tmp_path)
    runner.drive(env, team_no_jira)
    report = evaluate(env.store)

    assert report.source == "notes"
    assert (report.done_tasks, report.total_tasks) == (4, 6)
    assert not report.goal_accomplished
    assert report.closed_jira_minutes == report.total_jira_minutes == 0
    assert {t.id for t in report.remaining} == {"NOTES-4", "NOTES-5"}
    env.close()


def test_notes_project_not_done_despite_green_board(tmp_path):
    # Every filed ticket closes, but the notes show the project is twice the board.
    env = team_partial_jira.build(run_id="eval-pj", root=tmp_path)
    runner.drive(env, team_partial_jira)
    report = evaluate(env.store)

    assert report.source == "notes"
    assert (report.done_tasks, report.total_tasks) == (4, 6)
    assert not report.goal_accomplished
    assert report.closed_jira_minutes == report.total_jira_minutes == 1200  # the filed subset
    env.close()


def test_no_deadline_check(tmp_path):
    # A completion after the project deadline still counts: only doneness is graded.
    env = Env.make(run_id="eval-late", root=tmp_path)
    env.store.add_project(Project(id="late", name="Late", deadline_tick=10))
    repo = JiraRepository(env.store)
    repo.ensure_schema()
    repo.add_issue(Issue(id="LATE-1", project_id="late", issue_type="task",
                         title="Slipped", status="done", assignee_id=None,
                         estimate_minutes=60, updated_tick=20))

    report = evaluate(env.store)
    assert report.source == "board"
    assert report.done_tasks == report.total_tasks == 1
    assert report.goal_accomplished
    assert report.closed_jira_minutes == report.total_jira_minutes == 60
    env.close()


def test_project_resolution(tmp_path):
    env = Env.make(run_id="eval-proj", root=tmp_path)
    JiraRepository(env.store).ensure_schema()
    with pytest.raises(ConfigurationError):
        evaluate(env.store)  # no project at all

    env.store.add_project(Project(id="p1", name="One"))
    env.store.add_project(Project(id="p2", name="Two"))
    with pytest.raises(ConfigurationError):
        evaluate(env.store)  # ambiguous without an explicit project_id
    with pytest.raises(ConfigurationError):
        evaluate(env.store, project_id="nope")  # unknown project

    report = evaluate(env.store, project_id="p1")  # explicit id: no tasks anywhere
    assert report.total_tasks == 0 and not report.goal_accomplished
    env.close()
