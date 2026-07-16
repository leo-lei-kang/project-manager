"""The Dependency Blind 2 scenario: two engineers, zero slack, persona-decided.

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
from pm.npc.behavior import assignee_pickup_hook
from pm.npc.persona import CHAOTIC, DEPENDENCY_BLIND
from pm.scenarios.test_dependency_blind_2 import (
    DEPS,
    MEMBERS,
    PROJECT_ID,
    TASKS,
    build,
)
from pm.sim.clock import WEEK_END_TICK
from pm.sim.simulation import Simulation


def _run_week(env) -> JiraApi:
    api = JiraApi(JiraRepository(env.store), env.engine)
    Simulation(env).run(on_tick=assignee_pickup_hook(api, MEMBERS, PROJECT_ID))
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
        "SELECT MAX(done_tick) AS t FROM event WHERE type = 'jira_ticket'")["t"]
    assert last_done == WEEK_END_TICK
    dropped = env.store.db.query_one(
        "SELECT COUNT(*) AS n FROM event_log WHERE kind = 'event.dropped_past_week'")["n"]
    assert dropped == 0
    env.close()


def test_chaotic_personas_do_not_finish(tmp_path):
    # Random selection defers a blocker until the partner's ready pool runs dry;
    # with zero slack the idle time cannot be absorbed. Deterministic per seed.
    env = build(run_id="blind-chaos", root=tmp_path, member_persona=CHAOTIC)
    api = _run_week(env)

    assert env.clock.now() == WEEK_END_TICK
    tasks = api.search(project_id=PROJECT_ID, issue_type="task")
    done = sum(1 for t in tasks if t.status == "done")
    assert 0 < done < 16  # both worked all week, but the board didn't finish
    env.close()


def test_dependency_blind_works_blocked_tickets_out_of_order(tmp_path):
    # A dependency-blind engineer never idles (blocked tickets are candidates
    # too), so the board finishes — but dependents start before their blockers.
    env = build(run_id="blind-blind", root=tmp_path, member_persona=DEPENDENCY_BLIND)
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
    events = {
        json.loads(r["payload_json"])["issue_key"]: (r["start_tick"], r["done_tick"])
        for r in env.store.db.query_all(
            "SELECT payload_json, start_tick, done_tick FROM event "
            "WHERE type = 'jira_ticket'")
    }
    violations = [
        (blocker, dependent)
        for blocker, dependent in DEPS
        if events[keys[dependent]][0] < events[keys[blocker]][1]
    ]
    assert violations  # at least one dependent was worked before its blocker
    env.close()
