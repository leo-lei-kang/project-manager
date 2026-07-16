"""Tests for the visualization renderers (pm/viz/)."""

from __future__ import annotations

import json

import pytest

from pm.db.store import Store
from pm.jira.api import JiraApi
from pm.jira.models import Issue
from pm.jira.repository import JiraRepository
from pm.agent.log import AgentLog, agent_log_name
from pm.scenarios import team_no_jira, two_engineers
from pm.sim.engine import Engine
from pm.viz import write_agent_activity, write_calendars, write_jira_tasks
from pm.viz.agent_activity import render_agent_activity_html
from pm.viz.calendars import (
    Block,
    day_segments,
    load_blocks,
    render_calendars_html,
)
from pm.viz.jira_tasks import (
    graph_issues,
    render_jira_html,
    topo_order,
    work_spans,
)
from pm.world.models import Person, Project


def issue(key: str, deps: list[str] | None = None, priority: int = 2) -> Issue:
    return Issue(
        id=key,
        project_id="p",
        issue_type="task",
        title=f"Title {key}",
        depends_on=deps or [],
        priority=priority,
    )


# -- topo_order ---------------------------------------------------------------

def test_topo_order_chain():
    issues = [issue("P-3", ["P-2"]), issue("P-1"), issue("P-2", ["P-1"])]
    assert topo_order(issues) == ["P-1", "P-2", "P-3"]

def test_topo_order_diamond_respects_all_edges():
    issues = [
        issue("P-1"),
        issue("P-2", ["P-1"]),
        issue("P-3", ["P-1"]),
        issue("P-4", ["P-2", "P-3"]),
    ]
    order = topo_order(issues)
    assert order.index("P-1") < order.index("P-2")
    assert order.index("P-1") < order.index("P-3")
    assert order.index("P-2") < order.index("P-4")
    assert order.index("P-3") < order.index("P-4")

def test_topo_order_ties_break_by_priority_then_id():
    issues = [issue("P-2"), issue("P-3", priority=1), issue("P-1")]
    assert topo_order(issues) == ["P-3", "P-1", "P-2"]

def test_topo_order_cycle_raises():
    issues = [issue("P-1", ["P-2"]), issue("P-2", ["P-1"])]
    with pytest.raises(ValueError, match="cycle"):
        topo_order(issues)


# -- render_jira_html against a seeded db -------------------------------------

@pytest.fixture()
def api(tmp_path):
    store = Store.open(str(tmp_path / "world.db"), create=True)
    repo = JiraRepository(store)
    repo.ensure_schema()
    store.add_project(Project(id="checkout", name="Checkout"))
    store.add_person(Person(id="dana", name="Dana", role="Backend"))
    yield JiraApi(repo, Engine(store))
    store.close()

def test_render_jira_html_timeline_and_unscheduled(api):
    first = api.create_issue(
        "checkout", "task", "First", estimate_minutes=120, assignee="dana"
    )
    second = api.create_issue("checkout", "task", "Second", estimate_minutes=60)
    api.link_issue(second.id, first.id)  # first blocks second

    issues = graph_issues(api.search())
    order = topo_order(issues)
    # first was worked Mon 09:00-11:00; second never started.
    spans = {first.id: (0, 120)}
    page = render_jira_html(issues, order, spans, heading="test")

    completion = page.split("<ol>")[1]
    assert completion.index(first.id) < completion.index(second.id)
    assert "2h" in page  # format_hours(120)
    assert "@dana" in page
    # the worked bar spans 120 ticks at 0.5 px/tick, at the timeline origin
    assert 'width="60"' in page
    # the never-worked task lands in the Not scheduled section
    assert "Not scheduled" in page
    assert second.id in page.split("Not scheduled")[1]


def test_work_spans_reads_jira_ticket_events(tmp_path):
    store = Store.open(str(tmp_path / "world.db"), create=True)
    try:
        _insert_event(store, "jira_ticket", "alice", 0, 120,
                      {"issue_key": "GA-1"}, status="done")
        # a second event for the same key unions into one span
        _insert_event(store, "jira_ticket", "alice", 150, 30,
                      {"issue_key": "GA-1"}, status="done")
        _insert_event(store, "jira_ticket", "bob", 480, 60,
                      {"issue_key": "GA-2"}, status="active")
        # cancelled and instantaneous rows reserve nothing
        _insert_event(store, "jira_ticket", "bob", 600, 60,
                      {"issue_key": "GA-3"}, status="cancelled")
        _insert_event(store, "slack.send", "bob", 700, 0, {}, status="done")
        spans = work_spans(store)
    finally:
        store.close()
    assert spans == {"GA-1": (0, 180), "GA-2": (480, 540)}


# -- calendar pure functions ---------------------------------------------------

def test_day_segments_within_one_day():
    # Tue 11:00-11:30 == ticks 600..630.
    assert day_segments(Block("meeting", 600, 630, "x")) == [(1, 120, 30)]

def test_day_segments_splits_at_day_boundary():
    # Mon 16:30 .. Tue 09:30: 17:00 rolls into the next morning.
    assert day_segments(Block("jira_ticket", 450, 510, "x")) == [
        (0, 450, 30),
        (1, 0, 30),
    ]

def test_render_calendars_html_positions_blocks_and_keeps_empty_people():
    alice = Person(id="alice", name="Alice", role="Team Lead")
    bob = Person(id="bob", name="Bob")
    blocks = {"alice": [Block("meeting", 120, 150, "Daily standup")]}
    page = render_calendars_html([alice, bob], blocks, heading="test")
    assert "top:120px;height:30px" in page
    assert "Daily standup" in page
    assert "Bob" in page  # empty calendar still gets a section

def _insert_event(store, type_, owner, start, duration, payload, status="pending"):
    store.db.execute(
        "INSERT INTO event (type, owner_id, start_tick, duration, seq, status,"
        " payload_json) VALUES (?, ?, ?, ?, 0, ?, ?)",
        (type_, owner, start, duration, status, json.dumps(payload)),
    )

def test_load_blocks_normalizes_legacy_kinds(tmp_path):
    store = Store.open(str(tmp_path / "world.db"), create=True)
    try:
        store.add_person(Person(id="alice", name="Alice"))
        _insert_event(store, "meeting.start", "alice", 120, 30, {"title": "Standup"})
        blocks = load_blocks(store)
    finally:
        store.close()
    assert blocks == {"alice": [Block("meeting", 120, 150, "Standup")]}

def test_load_blocks_covers_completed_runs_and_attendees(tmp_path):
    store = Store.open(str(tmp_path / "world.db"), create=True)
    try:
        # A finished meeting still renders (occupancy is freed on completion),
        # and it occupies its attendees, not just the owner.
        _insert_event(store, "meeting", "alice", 120, 30,
                      {"title": "Standup", "attendees": ["alice", "bob"]},
                      status="done")
        # Instantaneous events (duration 0) reserve nothing.
        _insert_event(store, "slack.send", "alice", 200, 0, {}, status="done")
        blocks = load_blocks(store)
    finally:
        store.close()
    standup = Block("meeting", 120, 150, "Standup")
    assert blocks == {"alice": [standup], "bob": [standup]}


def test_render_agent_activity_html_markers_and_entries():
    entries = [
        {"tick": 0, "kind": "llm_call", "step": 0, "model": "m/1",
         "input_tokens": 100, "output_tokens": 20, "tool_calls": ["send_slack"]},
        {"tick": 0, "kind": "tool_call", "name": "send_slack",
         "args": {"channel_id": "eng"}},
        {"tick": 720, "kind": "llm_call", "step": 0, "model": "m/1",
         "input_tokens": 150, "output_tokens": 30, "tool_calls": []},
    ]
    page = render_agent_activity_html(entries, heading="run demo")

    assert "run demo" in page
    assert "2 LLM calls, 250 in / 50 out tokens" in page  # totals line
    assert page.count("<circle") == 3  # one marker per entry
    assert "Mon 09:00" in page and "Tue 13:00" in page  # tick labels (0 and 720)
    assert "send_slack" in page and "100 in / 20 out tokens" in page


# -- end to end ----------------------------------------------------------------

def test_write_agent_activity_reads_run_log(tmp_path):
    env = two_engineers.build(run_id="e2e-agent", root=tmp_path / "runs")
    env.close()
    run_dir = tmp_path / "runs" / "e2e-agent"

    assert write_agent_activity("e2e-agent", root=tmp_path / "runs") is None  # no log

    log = AgentLog(run_dir / agent_log_name("m/1"))
    log.append({"tick": 240, "kind": "llm_call", "step": 0, "model": "m/1",
                "input_tokens": 10, "output_tokens": 5, "tool_calls": []})
    out = write_agent_activity("e2e-agent", root=tmp_path / "runs")
    assert out == run_dir / "agent_activity.html"
    assert "Mon 13:00" in out.read_text()  # tick 240 rendered on the timeline


def test_writers_render_scenario_runs(tmp_path):
    # Calendars from the meeting-heavy team week; the board from the two-engineer run.
    env = team_no_jira.build(run_id="e2e-cal", root=tmp_path / "runs")
    env.close()
    cal_path = write_calendars("e2e-cal", root=tmp_path / "runs")
    assert cal_path == tmp_path / "runs" / "e2e-cal" / "calendars.html"
    calendars = cal_path.read_text()
    for name in ("Alice", "Bob", "Clare", "David", "Elieen"):
        assert name in calendars
    assert "Daily standup" in calendars

    env = two_engineers.build(run_id="e2e-jira", root=tmp_path / "runs")
    tasks = [i.id for i in JiraRepository(env.store).list_issues(issue_type="task")]
    env.close()
    jira_path = write_jira_tasks("e2e-jira", root=tmp_path / "runs")
    assert jira_path == tmp_path / "runs" / "e2e-jira" / "jira_tasks.html"
    jira = jira_path.read_text()
    assert len(tasks) == 16
    for key in tasks:
        assert key in jira
