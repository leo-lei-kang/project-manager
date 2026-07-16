"""Scripted OpenAI-compatible fakes for driving ``LLMAgent`` without a network.

``FakeClient`` cycles its responses forever, so one client can serve every
review-hook invocation across a simulated week (each ``LLMAgent.run`` consumes
responses until one has no tool call).
"""

from __future__ import annotations

import itertools
import json
from typing import Any


class _Fn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name, self.arguments = name, arguments


class _ToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id, self.function = id, _Fn(name, arguments)


class _Message:
    def __init__(self, content: str | None = None, tool_calls: list[Any] | None = None) -> None:
        self.content, self.tool_calls = content, tool_calls


class _Usage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens, self.completion_tokens = prompt_tokens, completion_tokens


class _Resp:
    def __init__(self, message: _Message, usage: _Usage) -> None:
        self.choices = [type("C", (), {"message": message})()]
        self.usage = usage


def tool_call_resp(name: str, args: dict[str, Any], *,
                   prompt_tokens: int = 100, completion_tokens: int = 20) -> _Resp:
    """A response whose message calls ``name(args)``."""
    call = _ToolCall("c1", name, json.dumps(args))
    return _Resp(_Message(tool_calls=[call]), _Usage(prompt_tokens, completion_tokens))


def text_resp(content: str, *,
              prompt_tokens: int = 100, completion_tokens: int = 10) -> _Resp:
    """A final-answer response with no tool call."""
    return _Resp(_Message(content=content), _Usage(prompt_tokens, completion_tokens))


class FakeClient:
    """Serves ``responses`` in a repeating cycle; counts the requests made."""

    def __init__(self, responses: list[_Resp]) -> None:
        self._cycle = itertools.cycle(responses)
        self.requests = 0
        self.chat = type("Chat", (), {"completions": self})()

    async def create(self, **kwargs: Any) -> _Resp:
        self.requests += 1
        return next(self._cycle)
