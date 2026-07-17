"""The review hook's triggers: daily cadence, meeting-end, and Slack mentions.

Uses a scripted OpenAI-shaped fake client (the ``client=`` seam the scenarios'
``agent_review_hook`` forwards to :func:`pm.agent.hook.llm_review_hook`), and
asserts on the ``agent.review.trigger`` rows the hook logs — one per firing,
with its reason.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from pm.env import Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.scenarios import single_engineer_with_agent as scenario
from pm.sim.events import SlackSendEvent
from pm.sim.npc import WorkDriver
from pm.sim.simulation import Simulation


def _response(content="Nothing needs steering.", tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls,
                          model_dump=lambda: {"role": "assistant", "content": content})
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                           usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))


class FakeClient:
    """Scripted client: pops ``replies`` in order, then no-op final replies."""

    def __init__(self, replies: list | None = None) -> None:
        self.calls = 0
        self._replies = replies or []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls += 1
        return self._replies.pop(0) if self._replies else _response()


def _triggers(env: Env) -> list[tuple[int, str]]:
    return [(e.sim_tick, e.payload["reason"]) for e in env.store.read_log()
            if e.kind == "agent.review.trigger"]


def test_slack_mention_triggers_review_and_unnamed_does_not(tmp_path):
    env = scenario.build(run_id="hook-slack", root=tmp_path)
    hook = scenario.agent_review_hook(env, client=FakeClient(), model="fake/model")
    sim = Simulation(env)
    env.engine.schedule(SlackSendEvent(
        owner_id="alice", start_tick=10,
        payload={"message_id": "m1", "channel_id": "eng",
                 "body": "PM, are we on track?"}))
    env.engine.schedule(SlackSendEvent(
        owner_id="alice", start_tick=40,
        payload={"message_id": "m2", "channel_id": "eng",
                 "body": "taking a stretch, back in five"}))

    for _ in range(60):
        hook(sim)
        sim.step()

    # tick 30 is Monday's standup ending; the unnamed message at 40 is silent.
    assert _triggers(env) == [(0, "cadence"), (10, "slack"), (30, "meeting_end")]
    env.close()


def test_agents_own_message_does_not_retrigger(tmp_path):
    # The cadence review at tick 0 posts a message that names "PM" (its own
    # signature); its completion must not count as a slack trigger.
    env = scenario.build(run_id="hook-self", root=tmp_path)
    send = SimpleNamespace(id="t1", function=SimpleNamespace(
        name="send_slack",
        arguments=json.dumps({"channel_id": "eng",
                              "body": "Status from your PM: all on track."})))
    fake = FakeClient(replies=[_response(content=None, tool_calls=[send]),
                               _response("posted")])
    hook = scenario.agent_review_hook(env, client=fake, model="fake/model")
    sim = Simulation(env)

    for _ in range(30):
        hook(sim)
        sim.step()

    assert _triggers(env) == [(0, "cadence")]
    env.close()


def test_week_fires_daily_meeting_end_and_cxo_push_triggers(tmp_path):
    # The full week, wired like runner.drive: one cadence review per morning,
    # meeting-end reviews after standups, and a slack review per 16:00 CxO push.
    env = scenario.build(run_id="hook-week", root=tmp_path)
    hook = scenario.agent_review_hook(env, client=FakeClient(), model="fake/model")
    api = JiraApi(JiraRepository(env.store), env.engine)
    driver = WorkDriver(api, scenario.MEMBERS, scenario.PROJECT_ID)
    env.engine.activities.on_activity_done = driver.on_activity_done
    env.engine.on_event_done = driver.on_event_done
    driver.sweep(env.engine)

    Simulation(env).run(on_tick=hook)

    triggers = _triggers(env)
    assert [t for t, r in triggers if r == "cadence"] == [0, 480, 960, 1440, 1920]
    assert [t for t, r in triggers if r == "slack"] == [
        420 + day * 480 for day in range(5)]  # xavier's daily 16:00 pushes
    assert sum(1 for _, r in triggers if r == "meeting_end") >= 3  # standups
    env.close()
