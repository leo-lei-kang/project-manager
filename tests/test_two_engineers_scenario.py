"""The Two Engineers scenario: two engineers, zero slack, persona-decided.

Every odd task blocks the partner's next even task just-in-time, so the run is
the proof: in dependency + priority order the last completion lands exactly on
tick 2400; a chaotic picker strands their partner idle and the week cannot
absorb it; a dependency-blind picker finishes but works blocked tickets before
the work they depend on.
"""

from __future__ import annotations

import json

from pm.db.store import Store
from pm.env.environment import Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.persona import FREE_SPIRIT
from pm.scenarios import runner
from pm.scenarios import test_two_engineers as scenario
from pm.scenarios.test_two_engineers import (
    DEPS,
    PROJECT_ID,
    TASKS,
    build,
)
from pm.sim.clock import WEEK_END_TICK


def _run_week(env) -> JiraApi:
    api = JiraApi(JiraRepository(env.store), env.engine)
    runner.drive(env, scenario)
    return api


def test_seed_db_shape(tmp_path):
    env = build(run_id="blind-seed", root=tmp_path)
    env.close()
    seed = Store.open(str(Env.seed_path("blind-seed", tmp_path)))

    people = seed.list_people()
    assert {p.id for p in people} == {"alice", "clare"}
    assert not any(p.is_agent for p in people)

    # no meetings at all — the week is pure ticket work
    assert seed.db.query_all("SELECT * FROM event WHERE type = 'meeting'") == []

    tasks = seed.db.query_all("SELECT * FROM issue WHERE issue_type = 'task'")
    assert len(tasks) == 16
    assert all(t["assignee_id"] for t in tasks)
    by_member: dict[str, list] = {}
    for t in tasks:
        by_member.setdefault(t["assignee_id"], []).append(t)
    assert {m: len(ts) for m, ts in by_member.items()} == {m: len(TASKS[m]) for m in TASKS}
    # each engineer carries exactly one full 40-hour week of estimates
    assert {m: sum(t["estimate_minutes"] for t in ts) for m, ts in by_member.items()} == {
        "alice": 2400, "clare": 2400,
    }

    # half of each engineer's tasks start blocked, and every edge crosses members
    expected_blocked = len({dependent for _, dependent in DEPS})
    assert expected_blocked == 8
    assert sum(1 for t in tasks if t["status"] == "blocked") == expected_blocked
    assert all(t["status"] in ("todo", "blocked") for t in tasks)
    assert all(blocker[0] != dependent[0] for blocker, dependent in DEPS)
    seed.close()


def test_default_personas_finish_exactly_at_week_end(tmp_path):
    # Worked in dependency + priority order, every handoff is just-in-time and
    # the board finishes at Fri 17:00 sharp.
    env = build(run_id="blind-run", root=tmp_path)
    api = _run_week(env)

    assert env.clock.now() == WEEK_END_TICK
    tasks = api.search(project_id=PROJECT_ID, issue_type="task")
    assert {t.status for t in tasks} == {"done"}
    assert sum(t.remaining_minutes for t in tasks) == 0
    last_done = env.store.db.query_one(
        "SELECT MAX(done_tick) AS t FROM activity WHERE kind = 'jira_work'")["t"]
    assert last_done == WEEK_END_TICK
    dropped = env.store.db.query_one(
        "SELECT COUNT(*) AS n FROM event_log WHERE kind = 'event.dropped_past_week'")["n"]
    assert dropped == 0
    env.close()


def test_free_spirit_works_blocked_tickets_out_of_order(tmp_path):
    # A free_spirit engineer ignores dependencies and never idles (blocked tickets
    # are candidates too), so the board finishes — but dependents start before their
    # blockers.
    env = build(run_id="blind-blind", root=tmp_path, member_persona=FREE_SPIRIT)
    api = _run_week(env)

    tasks = api.search(project_id=PROJECT_ID, issue_type="task")
    assert {t.status for t in tasks} == {"done"}

    titles_to_ordinal = {
        (member, title): ordinal
        for member, rows in TASKS.items()
        for ordinal, (title, _) in enumerate(rows, start=1)
    }
    keys = {
        (t.assignee_id, titles_to_ordinal[(t.assignee_id, t.title)]): t.id
        for t in tasks
    }
    spans = {
        json.loads(r["params_json"])["issue_key"]: (r["created_tick"], r["done_tick"])
        for r in env.store.db.query_all(
            "SELECT params_json, created_tick, done_tick FROM activity "
            "WHERE kind = 'jira_work'")
    }
    violations = [
        (blocker, dependent)
        for blocker, dependent in DEPS
        if spans[keys[dependent]][0] < spans[keys[blocker]][1]
    ]
    assert violations  # at least one dependent was worked before its blocker
    env.close()
