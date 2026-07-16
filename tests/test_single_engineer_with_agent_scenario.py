"""The Single Engineer with Agent scenario: a free-spirit engineer works the board
at random while the LLM PM reviews it every four sim-hours. Driven with a scripted
fake model (posts a fixed visibility-only highlight each review), so the run stays
hermetic; every review is logged to the run's agent.jsonl with token usage.
"""

from __future__ import annotations

from pm.agent.log import read_agent_logs
from pm.db.store import Store
from pm.env.environment import Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.sim.npc import WorkDriver
from pm.scenarios.single_engineer import HIGH_PRIORITY
from pm.scenarios.single_engineer_with_agent import (
    CHANNEL,
    MEMBERS,
    PROJECT_ID,
    REVIEW_PERIOD,
    agent_review_hook,
    build,
)
from pm.sim.clock import WEEK_END_TICK
from pm.sim.simulation import Simulation

from tests.llm_fakes import FakeClient, text_resp, tool_call_resp

_HIGHLIGHT = "High-priority still open — please prioritize."


def _fake_client() -> FakeClient:
    """Each review: one send_slack highlight (names no person, no directive), then done."""
    return FakeClient([
        tool_call_resp("send_slack", {"channel_id": CHANNEL, "body": _HIGHLIGHT},
                       prompt_tokens=100, completion_tokens=20),
        text_resp("posted a highlight", prompt_tokens=150, completion_tokens=10),
    ])


def _run(tmp_path, run_id="solo-agent"):
    env = build(run_id=run_id, root=tmp_path)  # default FREE_SPIRIT
    api = JiraApi(JiraRepository(env.store), env.engine)
    driver = WorkDriver(api, MEMBERS, PROJECT_ID)
    env.engine.activities.on_activity_done = driver.on_activity_done
    env.engine.on_event_done = driver.on_event_done
    driver.sweep(env.engine)  # kickoff
    Simulation(env).run(on_tick=agent_review_hook(env, client=_fake_client()))
    return env, api


def test_seed_db_shape(tmp_path):
    env = build(run_id="solo-agent-seed", root=tmp_path)
    env.close()
    seed = Store.open(str(Env.seed_path("solo-agent-seed", tmp_path)))

    # the implementer plus the agent that sends Slack (both needed for the FKs)
    assert {p.id for p in seed.list_people()} == {"alice", "pm"}
    assert [c["id"] for c in seed.db.query_all("SELECT id FROM channel")] == [CHANNEL]
    tasks = seed.db.query_all("SELECT * FROM issue WHERE issue_type = 'task'")
    assert len(tasks) == 11 and all(t["assignee_id"] == "alice" for t in tasks)
    seed.close()


def test_agent_posts_each_review_and_logs_jsonl(tmp_path):
    env, _ = _run(tmp_path)

    msgs = env.store.list_messages(CHANNEL)
    assert msgs  # the agent posted its highlight
    assert all(m.sender_id == "pm" for m in msgs)  # from the agent
    # every post lands on a review boundary (the event fires the tick after the review)
    assert all((m.sent_tick - 1) % REVIEW_PERIOD == 0 for m in msgs)
    assert all(m.body == _HIGHLIGHT for m in msgs)

    entries = read_agent_logs(tmp_path / "solo-agent")
    assert entries  # the run left an agent.jsonl next to world.db
    # every entry happened on a review boundary, stamped with the sim tick
    assert all(e["tick"] % REVIEW_PERIOD == 0 for e in entries)
    llm_calls = [e for e in entries if e["kind"] == "llm_call"]
    tool_calls = [e for e in entries if e["kind"] == "tool_call"]
    # each review is one loop: send_slack round-trip + final answer
    assert len(llm_calls) == 2 * len(tool_calls)
    assert all(e["input_tokens"] > 0 and e["output_tokens"] > 0 for e in llm_calls)
    assert all(e["name"] == "send_slack" for e in tool_calls)
    env.close()


def test_free_spirit_strands_high_priority(tmp_path):
    # The agent's highlights don't change the free-spirit engineer's random picks:
    # backlog work displaces project tasks, so the week ends at 6 of 11 done with
    # high-priority project work left unshipped.
    env, api = _run(tmp_path)

    assert env.clock.now() == WEEK_END_TICK
    tasks = api.search(project_id=PROJECT_ID, issue_type="task")
    assert sum(1 for t in tasks if t.status == "done") == 6
    assert [t for t in tasks if t.priority == HIGH_PRIORITY and t.status != "done"]
    env.close()
