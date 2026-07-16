"""The agent, over an in-process tool backend, works out who has the most tickets.

Two tests:
  * a hermetic check of `InProcessBackend` + the `LLMAgent` loop (scripted fake model);
  * a real-LLM integration test, gated on `OPENROUTER_API_KEY` / `OPENROUTER_MODEL`
    (skipped otherwise), driven by a customizable prompt (`PM_AGENT_PROMPT`).
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from pm.agent.openrouter_agent import (
    MODELS,
    InProcessBackend,
    LLMAgent,
    OPENROUTER_BASE_URL,
)
from pm.agent.tools import AgentTools
from pm.env import Env
from pm.npc.cast import seed_cast
from pm.world.models import Meeting, Project

# Seeded board: alice is the unambiguous top ticket-holder.
_TICKETS = {"alice": 3, "bob": 1, "clare": 1}
_TOP_HOLDER = "alice"
_DEFAULT_PROMPT = (
    "Which coworker is assigned the most Jira tickets on the 'checkout' board? "
    "Reply with just their first name."
)

@pytest.fixture
def backend(tmp_path):
    """An in-process backend over a seeded world (uneven ticket counts + meetings)."""
    env = Env.make(run_id="agent-llm", root=tmp_path)
    seed_cast(env.store)
    env.store.add_project(Project(id="checkout", name="Checkout"))
    tools = AgentTools(env)
    for assignee, count in _TICKETS.items():
        for i in range(count):
            tools.jira.create_issue("checkout", "task", f"{assignee}-task-{i}",
                                    estimate_minutes=60, assignee=assignee, actor="erin")
    env.store.add_meeting(Meeting(id="m1", title="Standup", start_tick=120, end_tick=150,
                                  attendees={"alice": "accepted", "bob": "accepted"}))
    yield InProcessBackend(tools), env
    env.close()


# -- hermetic --------------------------------------------------------------------

async def test_inprocess_backend_lists_and_dispatches(backend) -> None:
    b, _ = backend
    names = {t["function"]["name"] for t in await b.list_tools()}
    assert names == {"send_slack", "read_slack", "read_jira_board", "read_calendar"}

    board = json.loads(await b.call("read_jira_board", {"project_id": "checkout"}))
    counts: dict[str, int] = {}
    for issue in board["issues"]:
        counts[issue["assignee_id"]] = counts.get(issue["assignee_id"], 0) + 1
    assert counts == _TICKETS  # the board carries who-has-what


class _Fn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name, self.arguments = name, arguments


class _ToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id, self.function = id, _Fn(name, arguments)


class _Message:
    def __init__(self, content: str | None = None, tool_calls: list[Any] | None = None) -> None:
        self.content, self.tool_calls = content, tool_calls


class _Resp:
    def __init__(self, message: _Message) -> None:
        self.choices = [type("C", (), {"message": message})()]


class _FakeClient:
    """Scripted model: call read_jira_board, then answer with the top holder."""

    def __init__(self, responses: list[_Resp]) -> None:
        self._responses = responses
        self.chat = type("Chat", (), {"completions": self})()

    async def create(self, **kwargs: Any) -> _Resp:
        return self._responses.pop(0)


async def test_agent_loop_over_inprocess_backend(backend) -> None:
    b, _ = backend
    client = _FakeClient([
        _Resp(_Message(tool_calls=[_ToolCall("c1", "read_jira_board", '{"project_id": "checkout"}')])),
        _Resp(_Message(content=f"{_TOP_HOLDER} has the most tickets.")),
    ])
    agent = LLMAgent(client, "fake/model", b, max_steps=4)
    answer = await agent.run(_DEFAULT_PROMPT)
    assert _TOP_HOLDER in answer.lower()


# -- real LLM (opt-in) -----------------------------------------------------------

@pytest.mark.parametrize("model", MODELS)
async def test_llm_finds_top_ticket_holder(backend, model: str) -> None:
    pytest.importorskip("openai")
    try:
        from dotenv import load_dotenv

        load_dotenv()  # only for the API key (a secret); the model is the test param
    except ModuleNotFoundError:
        pass

    # Opt-in: this makes paid OpenRouter calls, so it stays off in normal runs even
    # when a key is present in .env.
    if not os.environ.get("PM_RUN_LLM_TESTS"):
        pytest.skip("set PM_RUN_LLM_TESTS=1 to run the real-LLM test (paid OpenRouter calls)")
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        pytest.skip("set OPENROUTER_API_KEY (in .env) to run the real-LLM test")

    from openai import AsyncOpenAI

    b, _ = backend
    client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
    prompt = os.environ.get("PM_AGENT_PROMPT", _DEFAULT_PROMPT)
    answer = await LLMAgent(client, model, b).run(prompt)

    assert answer.strip()  # the agent produced a final answer
    if prompt == _DEFAULT_PROMPT:
        assert _TOP_HOLDER in answer.lower()  # ...and named the right person
