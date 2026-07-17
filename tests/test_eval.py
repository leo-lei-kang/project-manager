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
from pm.world.models import Person, Project, Task

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
    # Notes hours come from the notes' own estimates (carried into the task
    # table by the kickoff payload); 25 tasks = 2220 min per DRI x 5.
    assert report.total_notes_minutes == 5 * 2220
    assert 0 < report.done_notes_minutes < report.total_notes_minutes
    assert "Notes tasks done:" in format_report(report)
    # The board holds only the low-priority backlog epic (10 x 4h), which the
    # otherwise board-idle members finish — the project itself never reaches it.
    assert report.closed_jira_minutes == report.total_jira_minutes == 2400
    assert len(report.remaining) == 10 and "NOTES-25" in {t.id for t in report.remaining}
    # Unmanaged run: nobody filed the notes tasks, and the backlog tickets
    # (different titles) match none of them.
    assert len(report.reconciliation) == 25
    assert all(r.jira_key is None for r in report.reconciliation)
    assert (report.notes_filed, report.notes_status_matched) == (0, 0)
    # Every board ticket sits under the backlog epic — no orphan row.
    assert all(e.id for e in report.epics)
    # Members really work their notes queues (time-burning activities with no
    # board writes): by Friday each has three done and the fourth in flight,
    # dispatched in numeric queue order.
    import json as _json
    rows = env.store.db.query_all(
        "SELECT attendees_json, params_json, state FROM activity "
        "WHERE kind = 'jira_work' AND params_json LIKE '%notes_id%' ORDER BY id")
    assert len(rows) == 20 and sum(1 for r in rows if r["state"] == "done") == 15
    bob = [_json.loads(r["params_json"])["notes_id"] for r in rows
           if _json.loads(r["attendees_json"]) == ["bob"]]
    assert bob == ["NOTES-1", "NOTES-7", "NOTES-8", "NOTES-9"]
    env.close()


def test_reconciliation_matches_notes_to_board_one_by_one(tmp_path):
    # Notes tasks pair with board tickets by normalized title (+ assignee tie
    # break); statuses are compared with jira in_review counting as in_progress.
    env = Env.make(run_id="eval-recon", root=tmp_path)
    env.store.add_project(Project(id="MT", name="MT"))
    for pid in ("alice", "bob"):
        env.store.add_person(Person(id=pid, name=pid))
    repo = JiraRepository(env.store)
    repo.ensure_schema()
    for tid, title, dri, status in (
        ("NOTES-1", "Build ingest", "alice", "done"),
        ("NOTES-2", "Ship viewer", "bob", "in_progress"),
        ("NOTES-3", "A11y review", "bob", "todo"),
        ("NOTES-4", "Search indexing", "alice", "in_progress"),
    ):
        env.store.upsert_task(Task(id=tid, title=title, dri_id=dri, status=status))
    # NOTES-1 filed, status agrees; NOTES-2 filed (normalized title), status
    # WRONG; NOTES-3 never filed; NOTES-4 filed, in_review counts as
    # in_progress; MT-9 is unrelated backlog and must match nothing.
    for key, title, status, assignee in (
        ("MT-1", "Build ingest", "done", "alice"),
        ("MT-2", "ship  VIEWER", "todo", "bob"),
        ("MT-4", "Search indexing", "in_review", "alice"),
        ("MT-9", "Refactor retry helpers", "done", "alice"),
    ):
        repo.add_issue(Issue(id=key, project_id="MT", issue_type="task",
                             title=title, status=status, assignee_id=assignee,
                             estimate_minutes=60))
    report = evaluate(env.store)

    rows = {r.notes_id: r for r in report.reconciliation}
    assert rows["NOTES-1"].jira_key == "MT-1" and rows["NOTES-1"].status_match
    assert rows["NOTES-2"].jira_key == "MT-2" and not rows["NOTES-2"].status_match
    assert rows["NOTES-3"].jira_key is None and not rows["NOTES-3"].status_match
    assert rows["NOTES-4"].jira_key == "MT-4" and rows["NOTES-4"].status_match
    assert "MT-9" not in {r.jira_key for r in report.reconciliation}
    assert (report.notes_filed, report.notes_status_matched) == (3, 2)

    # The four filed tickets sit under no epic — they get a synthetic
    # epic-progress row (MT-1 and MT-9 done of the four), listing each ticket.
    orphans = next(e for e in report.epics if e.id == "")
    assert (orphans.done_tasks, orphans.total_tasks) == (2, 4)
    assert [t.id for t in orphans.tasks] == ["MT-1", "MT-2", "MT-4", "MT-9"]

    text = format_report(report)
    assert "Tasks without an epic: 2/4" in text
    assert "filed 3/4" in text and "statuses agree 2/4" in text
    # every notes task gets its own mapping line, matched or not
    assert "NOTES-1" in text and "-> MT-1" in text
    assert "NOTES-3" in text and "not filed" in text
    assert "NOTES-2" in text and "notes=in_progress board=todo" in text and "MISMATCH" in text
    assert "NOTES-4" in text and "-> MT-4" in text
    assert to_dict(report)["reconciliation"][0]["notes_id"] == "NOTES-1"
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
