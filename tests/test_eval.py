"""The evaluator: hours completed, week-goal verdict, per-person breakdown."""

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
from pm.scenarios.tight_week import MEMBERS, PROJECT_ID, build
from pm.sim.simulation import Simulation
from pm.world.models import Project

EXPECTED_MINUTES = {"alice": 2025, "bob": 2115, "clare": 2115, "david": 2160, "elieen": 2160}
EXPECTED_COUNTS = {"alice": 16, "bob": 13, "clare": 13, "david": 12, "elieen": 12}


def _run(tmp_path, run_id, **kwargs):
    env = build(run_id=run_id, root=tmp_path, **kwargs)
    api = JiraApi(JiraRepository(env.store), env.engine)
    Simulation(env).run(on_tick=assignee_pickup_hook(api, MEMBERS, PROJECT_ID))
    return env


def test_accomplished_week(tmp_path):
    env = _run(tmp_path, "eval-ok")
    report = evaluate(env.store)

    assert report.goal_accomplished
    assert report.project_id == PROJECT_ID
    assert report.done_tasks == report.total_tasks == 66
    assert report.done_minutes == report.total_minutes == 10575  # 176.25h
    assert report.last_done_tick == 2400 and report.deadline_tick == 2400
    assert report.remaining == []
    assert {p.person_id: len(p.done) for p in report.people} == EXPECTED_COUNTS
    assert {p.person_id: p.done_minutes for p in report.people} == EXPECTED_MINUTES
    assert all(p.remaining == [] for p in report.people)

    text = format_report(report)
    assert "ACCOMPLISHED" in text and "176.25h" in text and "none" in text
    env.close()


def test_unfinished_week(tmp_path):
    env = _run(tmp_path, "eval-chaos", member_persona=FREE_SPIRIT)
    report = evaluate(env.store)

    assert not report.goal_accomplished
    assert 0 < report.done_tasks < 66
    assert 0 < report.done_minutes < report.total_minutes
    assert report.remaining  # unfinished tasks are reported
    # every remaining task is carried in exactly one person's remaining list
    carried = [t.id for p in report.people for t in p.remaining]
    assert sorted(carried) == sorted(t.id for t in report.remaining)
    assert "NOT ACCOMPLISHED" in format_report(report)
    env.close()


def test_deadline_miss(tmp_path):
    # All tasks done, but the last completion lands after the project deadline.
    env = Env.make(run_id="eval-late", root=tmp_path)
    env.store.add_project(Project(id="late", name="Late", deadline_tick=10))
    repo = JiraRepository(env.store)
    repo.ensure_schema()
    repo.add_issue(Issue(id="LATE-1", project_id="late", issue_type="task",
                         title="Slipped", status="done", assignee_id=None,
                         estimate_minutes=60, updated_tick=20))

    report = evaluate(env.store)
    assert report.done_tasks == report.total_tasks == 1
    assert report.last_done_tick == 20 and report.deadline_tick == 10
    assert not report.goal_accomplished
    assert "missed" in format_report(report)
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

    report = evaluate(env.store, project_id="p1")  # explicit id: empty board
    assert report.total_tasks == 0 and not report.goal_accomplished
    env.close()
