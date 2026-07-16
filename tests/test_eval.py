"""The evaluator: project-completion verdict, closed Jira hours, per-person breakdown."""

from __future__ import annotations

import pytest

from pm.agent.log import AgentLog, agent_log_name
from pm.env.environment import Env
from pm.eval import evaluate, format_report, to_dict
from pm.exceptions import ConfigurationError
from pm.jira.models import Issue
from pm.jira.repository import JiraRepository
from pm.npc.persona import PERFECT
from pm.scenarios import runner, team_no_jira, two_engineers
from pm.scenarios.two_engineers import PROJECT_ID, build
from pm.world.models import Person, Project

EXPECTED_COUNTS = {"alice": 8, "clare": 8}


def test_board_project_done(tmp_path):
    # The two-engineer board worked perfectly: every ticket closes.
    env = build(run_id="eval-ok", root=tmp_path, member_persona=PERFECT)
    runner.drive(env, two_engineers)
    report = evaluate(env.store)

    assert report.goal_accomplished
    assert report.source == "board"  # no informal tasks: the Jira board stands in
    assert report.project_id == PROJECT_ID
    assert report.done_tasks == report.total_tasks == 16
    assert report.closed_jira_minutes == report.total_jira_minutes == 4800  # 80h
    assert report.remaining == []
    assert {p.person_id: len(p.done) for p in report.people} == EXPECTED_COUNTS
    assert all(p.remaining == [] for p in report.people)

    text = format_report(report)
    assert "PROJECT DONE" in text and "NOT DONE" not in text
    assert "80h" in text and "none" in text
    env.close()


def test_board_project_unfinished(tmp_path):
    # A half-done board, seeded directly: the unfinished-report fields.
    env = Env.make(run_id="eval-chaos", root=tmp_path)
    env.store.add_project(Project(id="GA", name="Live Transcription GA"))
    for pid in ("alice", "clare"):
        env.store.add_person(Person(id=pid, name=pid))
    repo = JiraRepository(env.store)
    repo.ensure_schema()
    repo.add_issue(Issue(id="GA-1", project_id="GA", issue_type="task", title="Shipped",
                         status="done", assignee_id="alice", estimate_minutes=60,
                         updated_tick=100))
    repo.add_issue(Issue(id="GA-2", project_id="GA", issue_type="task", title="Mid-flight",
                         status="in_progress", assignee_id="alice", estimate_minutes=90))
    repo.add_issue(Issue(id="GA-3", project_id="GA", issue_type="task", title="Untouched",
                         status="todo", assignee_id="clare", estimate_minutes=120))
    report = evaluate(env.store)

    assert not report.goal_accomplished
    assert (report.done_tasks, report.total_tasks) == (1, 3)
    assert 0 < report.closed_jira_minutes < report.total_jira_minutes
    assert report.remaining  # unfinished tasks are reported
    # every remaining task is carried in exactly one person's remaining list
    carried = [t.id for p in report.people for t in p.remaining]
    assert sorted(carried) == sorted(t.id for t in report.remaining)
    assert "PROJECT NOT DONE" in format_report(report)
    env.close()


def test_notes_project_not_done_despite_empty_board(tmp_path):
    # The board says nothing is happening; the notes show the project at 15/25.
    env = team_no_jira.build(run_id="eval-nj", root=tmp_path)
    runner.drive(env, team_no_jira)
    report = evaluate(env.store)

    assert report.source == "notes"
    assert (report.done_tasks, report.total_tasks) == (15, 25)
    assert not report.goal_accomplished
    # The board holds only the low-priority backlog epic (10 x 4h), which the
    # otherwise board-idle members finish — the project itself never reaches it.
    assert report.closed_jira_minutes == report.total_jira_minutes == 2400
    assert len(report.remaining) == 10 and "NOTES-25" in {t.id for t in report.remaining}
    env.close()


def test_agent_token_usage_from_jsonl(tmp_path):
    # run_dir sums the llm_call entries of the run's agent.jsonl into the report.
    env = Env.make(run_id="eval-agent", root=tmp_path)
    env.store.add_project(Project(id="p1", name="One"))
    JiraRepository(env.store).ensure_schema()
    run_dir = tmp_path / "eval-agent"
    log = AgentLog(run_dir / agent_log_name("fake-model"))
    log.append({"tick": 0, "kind": "llm_call", "input_tokens": 100, "output_tokens": 20})
    log.append({"tick": 0, "kind": "tool_call", "name": "send_slack"})
    log.append({"tick": 240, "kind": "llm_call", "input_tokens": 150, "output_tokens": 30})

    report = evaluate(env.store, run_dir=run_dir)
    assert (report.agent_llm_calls, report.agent_input_tokens,
            report.agent_output_tokens) == (2, 250, 50)
    assert "Agent LLM usage: 2 calls, 250 in / 50 out tokens" in format_report(report)
    assert to_dict(report)["agent"] == {
        "llm_calls": 2, "input_tokens": 250, "output_tokens": 50}

    # without run_dir (or without a log) the usage is zero and the line is omitted
    silent = evaluate(env.store)
    assert silent.agent_llm_calls == 0
    assert "Agent LLM usage" not in format_report(silent)
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
