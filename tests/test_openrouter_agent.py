"""The LLM agent loop — model → tool_call → result → final answer.

Uses fakes for the model client and tool backend, so it needs no network, no
OpenRouter key, and no running MCP server.
"""

from __future__ import annotations

from typing import Any

from pm.agent.openrouter_agent import LLMAgent


class _Fn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.function = _Fn(name, arguments)


class _Message:
    def __init__(self, content: str | None = None, tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _Resp:
    def __init__(self, message: _Message) -> None:
        self.choices = [type("C", (), {"message": message})()]


class _FakeClient:
    """Returns a scripted sequence of responses; records the tools it was given."""

    def __init__(self, responses: list[_Resp]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []
        self.chat = type("Chat", (), {"completions": self})()

    async def create(self, **kwargs: Any) -> _Resp:
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[dict[str, Any]]:
        return [{
            "type": "function",
            "function": {
                "name": "read_jira_board",
                "description": "read the board",
                "parameters": {"type": "object", "properties": {"project_id": {"type": "string"}}},
            },
        }]

    async def call(self, name: str, args: dict[str, Any]) -> str:
        self.calls.append((name, args))
        return "board: API is todo"


async def test_agent_dispatches_tool_then_returns_final_text() -> None:
    backend = _FakeBackend()
    client = _FakeClient([
        _Resp(_Message(tool_calls=[_ToolCall("c1", "read_jira_board", '{"project_id": "checkout"}')])),
        _Resp(_Message(content="Backend task API is still to-do; nudged the team.")),
    ])
    agent = LLMAgent(client, "test/model", backend, max_steps=5)

    out = await agent.run("check the board")

    assert backend.calls == [("read_jira_board", {"project_id": "checkout"})]
    assert out == "Backend task API is still to-do; nudged the team."
    # the tool schema from the backend was handed to the model
    assert client.calls[0]["tools"][0]["function"]["name"] == "read_jira_board"


async def test_agent_finishes_immediately_when_no_tool_call() -> None:
    backend = _FakeBackend()
    client = _FakeClient([_Resp(_Message(content="nothing to do"))])
    agent = LLMAgent(client, "test/model", backend)

    assert await agent.run("status?") == "nothing to do"
    assert backend.calls == []
