"""The Single Engineer with Agent scenario: a free-spirit engineer works the board
at random while the pm agent reviews it every four sim-hours and posts Slack
highlights of the still-open high-priority tickets — only when there is something
new to say. The agent raises visibility; it does not reorder the engineer's picks.
"""

from __future__ import annotations

from pm.db.store import Store
from pm.env.environment import Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.behavior import assignee_pickup_hook, compose
from pm.scenarios.test_single_engineer import HIGH_PRIORITY
from pm.scenarios.test_single_engineer_with_agent import (
    CHANNEL,
    MEMBERS,
    PROJECT_ID,
    REVIEW_PERIOD,
    agent_review_hook,
    build,
)
from pm.sim.clock import WEEK_END_TICK
from pm.sim.simulation import Simulation


def _run(tmp_path, run_id="solo-agent"):
    env = build(run_id=run_id, root=tmp_path)  # default FREE_SPIRIT
    api = JiraApi(JiraRepository(env.store), env.engine)
    Simulation(env).run(on_tick=compose(
        assignee_pickup_hook(api, MEMBERS, PROJECT_ID), agent_review_hook(env)))
    return env, api


def test_seed_db_shape(tmp_path):
    env = build(run_id="solo-agent-seed", root=tmp_path)
    env.close()
    seed = Store.open(str(Env.seed_path("solo-agent-seed", tmp_path)))

    # the implementer plus the agent that sends Slack (both needed for the FKs)
    assert {p.id for p in seed.list_people()} == {"alice", "pm"}
    assert [c["id"] for c in seed.db.query_all("SELECT id FROM channel")] == [CHANNEL]
    tasks = seed.db.query_all("SELECT * FROM issue WHERE issue_type = 'task'")
    assert len(tasks) == 12 and all(t["assignee_id"] == "alice" for t in tasks)
    seed.close()


def test_agent_highlights_high_priority(tmp_path):
    env, _ = _run(tmp_path)

    msgs = env.store.list_messages(CHANNEL)
    assert msgs  # the agent posted at least one highlight
    assert all(m.sender_id == "pm" for m in msgs)  # from the agent
    # every post lands on a review boundary (the event fires the tick after the review)
    assert all((m.sent_tick - 1) % REVIEW_PERIOD == 0 for m in msgs)
    # the highlight names a high-priority ticket and asks to prioritize
    assert f"{PROJECT_ID}-" in msgs[0].body and "prioritize" in msgs[0].body
    # deduped: consecutive posts never repeat the identical body
    assert all(a.body != b.body for a, b in zip(msgs, msgs[1:]))
    env.close()


def test_free_spirit_strands_high_priority(tmp_path):
    # The agent's highlights don't change the free-spirit engineer's random picks:
    # 8 of 12 tasks fit the week and some launch blockers are left unshipped.
    env, api = _run(tmp_path)

    assert env.clock.now() == WEEK_END_TICK
    tasks = api.search(project_id=PROJECT_ID, issue_type="task")
    assert sum(1 for t in tasks if t.status == "done") == 8
    assert [t for t in tasks if t.priority == HIGH_PRIORITY and t.status != "done"]
    env.close()
