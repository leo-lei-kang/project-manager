"""The Single Engineer scenario: 60 h of tickets in a 40-h week — triage matters.

All tasks are 300 minutes, so every run completes exactly eight of the twelve;
the persona decides which eight. PERFECT ships all seven launch blockers; the
FREE_SPIRIT provably leaves high-priority work over (deterministic per the run
seed).
"""

from __future__ import annotations

from pm.db.store import Store
from pm.env.environment import Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.behavior import assignee_pickup_hook
from pm.npc.persona import PERFECT, FREE_SPIRIT
from pm.scenarios.test_single_engineer import (
    HIGH,
    HIGH_PRIORITY,
    LOW,
    MEMBERS,
    PROJECT_ID,
    build,
)
from pm.sim.clock import WEEK_END_TICK
from pm.sim.simulation import Simulation


def _run(tmp_path, run_id, **kwargs):
    env = build(run_id=run_id, root=tmp_path, **kwargs)
    api = JiraApi(JiraRepository(env.store), env.engine)
    Simulation(env).run(on_tick=assignee_pickup_hook(api, MEMBERS, PROJECT_ID))
    return env, api


def test_seed_db_shape(tmp_path):
    env = build(run_id="solo-seed", root=tmp_path)
    env.close()
    seed = Store.open(str(Env.seed_path("solo-seed", tmp_path)))

    tasks = seed.db.query_all("SELECT * FROM issue WHERE issue_type = 'task'")
    assert len(tasks) == len(HIGH) + len(LOW) == 12
    assert all(t["assignee_id"] == "alice" for t in tasks)
    # the board overloads the 2400-tick week; the blockers alone fit within it
    assert sum(t["estimate_minutes"] for t in tasks) == 3600
    high = [t for t in tasks if t["priority"] == HIGH_PRIORITY]
    assert len(high) == 7 and sum(t["estimate_minutes"] for t in high) == 2100
    seed.close()


def test_perfect_ships_all_high_priority(tmp_path):
    env, api = _run(tmp_path, "solo-perfect", member_persona=PERFECT)

    assert env.clock.now() == WEEK_END_TICK
    tasks = api.search(project_id=PROJECT_ID, issue_type="task")
    high = [t for t in tasks if t.priority == HIGH_PRIORITY]
    assert all(t.status == "done" for t in high)  # every launch blocker shipped
    # 8 x 300 min fill the 2400-tick week exactly: the 7 blockers + 1 backlog item
    assert sum(1 for t in tasks if t.status == "done") == 8
    env.close()


def test_free_spirit_leaves_high_priority_over(tmp_path):
    env, api = _run(tmp_path, "solo-chaos", member_persona=FREE_SPIRIT)

    assert env.clock.now() == WEEK_END_TICK
    tasks = api.search(project_id=PROJECT_ID, issue_type="task")
    high_left = [t for t in tasks if t.priority == HIGH_PRIORITY and t.status != "done"]
    assert high_left  # random triage strands launch blockers
    assert sum(1 for t in tasks if t.status == "done") == 8  # worked all week regardless
    env.close()
