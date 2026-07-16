"""The Single Engineer scenario: the 40-h transcripts project + 20 h of backlog.

The six project_single_engineer.md tasks (2400 min, priority 1) fill the week
exactly; the board also holds five 240-minute backlog tickets it cannot absorb.
PERFECT ships the whole project and nothing else; the FREE_SPIRIT lets backlog
picks displace project work (deterministic per the run seed).
"""

from __future__ import annotations

from pm.db.store import Store
from pm.env.environment import Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.persona import PERFECT, FREE_SPIRIT
from pm.scenarios import runner
from pm.scenarios import test_single_engineer_free_spirit as scenario
from pm.scenarios.test_single_engineer_free_spirit import (
    HIGH,
    HIGH_PRIORITY,
    LOW,
    PROJECT_ID,
    build,
)
from pm.sim.clock import WEEK_END_TICK


def _run(tmp_path, run_id, **kwargs):
    env = build(run_id=run_id, root=tmp_path, **kwargs)
    api = JiraApi(JiraRepository(env.store), env.engine)
    runner.drive(env, scenario)
    return env, api


def test_seed_db_shape(tmp_path):
    env = build(run_id="solo-seed", root=tmp_path)
    env.close()
    seed = Store.open(str(Env.seed_path("solo-seed", tmp_path)))

    tasks = seed.db.query_all("SELECT * FROM issue WHERE issue_type = 'task'")
    assert len(tasks) == len(HIGH) + len(LOW) == 11
    assert all(t["assignee_id"] == "alice" for t in tasks)
    # the board overloads the 2400-tick week; the project alone fills it exactly
    assert sum(t["estimate_minutes"] for t in tasks) == 3600
    high = [t for t in tasks if t["priority"] == HIGH_PRIORITY]
    assert len(high) == 6 and sum(t["estimate_minutes"] for t in high) == 2400
    seed.close()


def test_perfect_ships_the_project(tmp_path):
    env, api = _run(tmp_path, "solo-perfect", member_persona=PERFECT)

    assert env.clock.now() == WEEK_END_TICK
    tasks = api.search(project_id=PROJECT_ID, issue_type="task")
    high = [t for t in tasks if t.priority == HIGH_PRIORITY]
    assert all(t.status == "done" for t in high)  # the whole project shipped
    # the six project tasks fill the 2400-tick week exactly: no backlog fits
    assert sum(1 for t in tasks if t.status == "done") == 6
    assert max(t.updated_tick for t in high) == WEEK_END_TICK  # zero slack
    env.close()


def test_free_spirit_leaves_project_work_over(tmp_path):
    env, api = _run(tmp_path, "solo-chaos", member_persona=FREE_SPIRIT)

    assert env.clock.now() == WEEK_END_TICK
    tasks = api.search(project_id=PROJECT_ID, issue_type="task")
    high_left = [t for t in tasks if t.priority == HIGH_PRIORITY and t.status != "done"]
    assert len(high_left) == 2  # backlog picks displaced project tasks
    assert sum(1 for t in tasks if t.status == "done") == 6  # worked all week regardless
    env.close()
