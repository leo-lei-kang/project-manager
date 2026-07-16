"""The Team with Jira scenario: a board the team can *barely* finish in order.

The intended schedule tiles every member's free calendar segments exactly, so
the run itself is the proof: any arithmetic slip in the task tables or the
dependency edges strands minutes behind a meeting, pushes the last completion
past tick 2400, and the all-done assertion fails.
"""

from __future__ import annotations

import json

from pm.db.store import Store
from pm.env.environment import Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.persona import FREE_SPIRIT
from pm.scenarios import runner
from pm.scenarios import team_with_jira as scenario
from pm.scenarios.team_with_jira import DEPS, PROJECT_ID, TASKS, build
from pm.sim.clock import WEEK_END_TICK

# Usable work capacity per member: 2400-tick week minus their meeting load.
CAPACITY = {"alice": 2025, "bob": 2115, "clare": 2115, "david": 2160, "elieen": 2160}


def test_seed_db_shape(tmp_path):
    env = build(run_id="tight-seed", root=tmp_path)
    env.close()
    seed = Store.open(str(Env.seed_path("tight-seed", tmp_path)))

    tasks = seed.db.query_all("SELECT * FROM issue WHERE issue_type = 'task'")
    assert len(tasks) == 66
    assert all(t["assignee_id"] for t in tasks)
    by_member: dict[str, list] = {}
    for t in tasks:
        by_member.setdefault(t["assignee_id"], []).append(t)
    assert {m: len(ts) for m, ts in by_member.items()} == {m: len(TASKS[m]) for m in TASKS}
    # every member's estimates sum to exactly their meeting-free capacity
    assert {m: sum(t["estimate_minutes"] for t in ts) for m, ts in by_member.items()} == CAPACITY

    # the 11 meetings are queued, and exactly the dependents of DEPS start blocked
    assert len(seed.db.query_all("SELECT * FROM event WHERE type = 'meeting'")) == 11
    expected_blocked = len({dependent for _, dependent in DEPS})
    assert sum(1 for t in tasks if t["status"] == "blocked") == expected_blocked
    assert all(t["status"] in ("todo", "blocked") for t in tasks)
    seed.close()


def test_default_personas_finish_exactly_at_week_end(tmp_path):
    # Worked in dependency + priority order, the board finishes at Fri 17:00 sharp.
    env = build(run_id="tight-run", root=tmp_path)
    api = JiraApi(JiraRepository(env.store), env.engine)

    runner.drive(env, scenario)

    assert env.clock.now() == WEEK_END_TICK
    tasks = api.search(project_id=PROJECT_ID, issue_type="task")
    assert {t.status for t in tasks} == {"done"}
    assert sum(t.remaining_minutes for t in tasks) == 0
    # "barely": the last completion lands exactly on the final tick of the week
    last_done = env.store.db.query_one(
        "SELECT MAX(done_tick) AS t FROM activity WHERE kind = 'jira_work'")["t"]
    assert last_done == WEEK_END_TICK
    # nothing was pushed past the week
    dropped = env.store.db.query_one(
        "SELECT COUNT(*) AS n FROM event_log WHERE kind = 'event.dropped_past_week'")["n"]
    assert dropped == 0
    env.close()


def test_free_spirit_personas_finish_but_violate_dependency_order(tmp_path):
    # Completion-driven activities make interruptions lossless (a meeting pauses
    # work, which resumes with its remaining minutes intact), so even random
    # selection completes a zero-slack board — but it works blocked tickets
    # before the work they depend on. Deterministic per seed.
    env = build(run_id="tight-chaos", root=tmp_path, member_persona=FREE_SPIRIT)
    api = JiraApi(JiraRepository(env.store), env.engine)

    runner.drive(env, scenario)

    assert env.clock.now() == WEEK_END_TICK
    tasks = api.search(project_id=PROJECT_ID, issue_type="task")
    assert {t.status for t in tasks} == {"done"}
    spans = {
        json.loads(r["params_json"])["issue_key"]: (r["created_tick"], r["done_tick"])
        for r in env.store.db.query_all(
            "SELECT params_json, created_tick, done_tick FROM activity "
            "WHERE kind = 'jira_work'")
    }
    titles_to_ordinal = {
        (member, title): ordinal
        for member, rows in TASKS.items()
        for ordinal, (title, _) in enumerate(rows, start=1)
    }
    keys = {
        (t.assignee_id, titles_to_ordinal[(t.assignee_id, t.title)]): t.id
        for t in tasks
    }
    violations = [
        (blocker, dependent)
        for blocker, dependent in DEPS
        if spans[keys[dependent]][0] < spans[keys[blocker]][1]
    ]
    assert violations  # at least one dependent started before its blocker finished
    env.close()
