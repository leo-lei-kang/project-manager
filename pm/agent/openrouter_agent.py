"""An LLM agent that drives the PM tools in-process, backed by OpenRouter.

Hands the :class:`~pm.agent.tools.AgentTools` schemas to an OpenRouter-hosted model
(OpenAI-compatible), then loops **model → tool_calls → AgentTools call → results**
until the model replies with no tool call. Everything runs in one process — no
server or transport.

This module is a library, not an entry point: ``examples/run_agent_llm.py`` drives
the loop against a seeded board (``OPENROUTER_API_KEY`` in ``.env``), and scenario
code can do the same. ``LLMAgent`` itself is dependency-free (duck-typed model
client + tool backend).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Curated OpenRouter model ids the agent can be run with (see examples/run_agent_llm.py).
# Any OpenRouter model id works; these are one flagship per vendor plus a couple of
# free (no-cost) models.
MODELS = [
    # one per vendor (paid) — each vendor's flagship / top tier
    "anthropic/claude-opus-4.8",
    "openai/gpt-5.5-pro",
    "google/gemini-3.1-pro-preview",
    "x-ai/grok-4.5",
    "deepseek/deepseek-v4-pro",
    "meta-llama/llama-4-maverick",
    "mistralai/mistral-large",
    "qwen/qwen3.7-max",
    # free variants (no cost to run)
    "openai/gpt-oss-20b:free",
    "google/gemma-4-31b-it:free",
]
_SYSTEM = (
    "You are a project manager for a small SaaS team. Use the available tools to "
    "read the Jira board, read and send Slack messages, and read the calendar, then "
    "keep the project moving. Coworkers only notice Slack messages that mention "
    "their name — when you ask someone to do something, address them by name in "
    "the message body (e.g. \"alice, please pick up MT-3\"). When you are done, "
    "reply with a short summary and no tool call."
)


class ToolBackend(Protocol):
    """What :class:`LLMAgent` needs from a tool source."""

    async def list_tools(self) -> list[dict[str, Any]]: ...
    async def call(self, name: str, args: dict[str, Any]) -> str: ...


class LLMAgent:
    """Runs the model↔tool loop over any OpenAI-compatible client + tool backend."""

    def __init__(
        self, client: Any, model: str, backend: ToolBackend,
        *, system: str = _SYSTEM, max_steps: int = 12,
        log: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.backend = backend
        self.system = system
        self.max_steps = max_steps
        self.log = log

    def _log(self, entry: dict[str, Any]) -> None:
        if self.log is not None:
            self.log(entry)

    async def run(self, goal: str) -> str:
        """Pursue ``goal`` with the tools; return the model's final text."""
        tools = await self.backend.list_tools()
        messages: list[Any] = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": goal},
        ]
        sent_from = 0  # messages[sent_from:] = what this round-trip newly sent
        for step in range(self.max_steps):
            new_input = messages[sent_from:]
            self._log({"kind": "llm_request", "step": step, "model": self.model,
                       "messages": len(messages), "input": new_input})
            resp = await self.client.chat.completions.create(
                model=self.model, messages=messages, tools=tools or None,
            )
            msg = resp.choices[0].message
            messages.append(msg.model_dump() if hasattr(msg, "model_dump") else msg)
            # The assistant reply is this entry's ``output``, not the next ``input``.
            sent_from = len(messages)
            calls = getattr(msg, "tool_calls", None)
            usage = getattr(resp, "usage", None)
            self._log({
                "kind": "llm_call", "step": step, "model": self.model,
                "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                "tool_calls": [tc.function.name for tc in calls] if calls else [],
                "output": {
                    "content": msg.content,
                    "tool_calls": [
                        {"name": tc.function.name,
                         "args": json.loads(tc.function.arguments or "{}")}
                        for tc in (calls or [])
                    ],
                },
            })
            if not calls:
                return msg.content or ""
            for tc in calls:
                args = json.loads(tc.function.arguments or "{}")
                self._log({"kind": "tool_call", "name": tc.function.name, "args": args})
                result = await self.backend.call(tc.function.name, args)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
                )
        return "(reached max steps without a final answer)"


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


# The AgentTools capabilities as OpenAI function schemas (see pm/agent/tools.py).
_AGENT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    _tool("send_slack", "Post a message to a Slack channel. People only read "
          "messages that name them — address the recipient by name in the body.",
          {"channel_id": {"type": "string"}, "body": {"type": "string"}},
          ["channel_id", "body"]),
    _tool("read_slack", "Read the messages in a Slack channel, oldest first. Pass "
          "since_tick (the last tick you reviewed) to get only the new messages.",
          {"channel_id": {"type": "string"},
           "since_tick": {"type": "integer",
                          "description": "Only messages sent at or after this tick."}},
          ["channel_id"]),
    _tool("read_jira_board", "Read a project's Jira board: the open issues (key, title, "
          "status, assignee, priority, estimates, depends_on) and a status breakdown "
          "covering all issues.",
          {"project_id": {"type": "string"}}, ["project_id"]),
    _tool("read_calendar", "Read the meetings a person attends (default: the agent).",
          {"person_id": {"type": "string"}}, []),
    _tool("read_transcripts", "List the meeting transcripts available so far (id, title, "
          "time, one-line preview); a transcript appears when its meeting ends. Pass "
          "since_tick to list only newly available ones; fetch a body with read_transcript.",
          {"since_tick": {"type": "integer",
                          "description": "Only transcripts available at or after this tick."}},
          []),
    _tool("read_transcript", "Read one meeting's full transcript body by meeting_id.",
          {"meeting_id": {"type": "string"}}, ["meeting_id"]),
]


class InProcessBackend:
    """A :class:`ToolBackend` that calls :class:`~pm.agent.tools.AgentTools` directly.

    Exposes the agent tools with no server/transport — the way to drive
    :class:`LLMAgent` locally (from examples or scenario code).
    """

    def __init__(self, tools: Any) -> None:
        self._tools = tools

    async def list_tools(self) -> list[dict[str, Any]]:
        return list(_AGENT_TOOL_SCHEMAS)

    async def call(self, name: str, args: dict[str, Any]) -> str:
        method = getattr(self._tools, name, None)
        if method is None:
            return json.dumps({"error": f"unknown tool {name!r}"})
        return json.dumps(method(**args))
