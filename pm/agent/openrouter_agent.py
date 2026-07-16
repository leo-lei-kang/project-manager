"""An LLM agent that drives the PM tools over MCP, backed by OpenRouter.

Mirrors fleet-sdk's MCP-client agent loop: connect to the tools' MCP server
(``pm-mcp``), list its tools, hand their schemas to an OpenRouter-hosted model
(OpenAI-compatible), then loop **model → tool_calls → MCP call_tool → results**
until the model replies with no tool call.

Config is read from the environment (a local ``.env`` is loaded if present):

  * ``OPENROUTER_API_KEY`` — required; your OpenRouter key.
  * ``OPENROUTER_MODEL``   — required; any OpenRouter model id (configurable).
  * ``PM_MCP_URL``         — the tools server URL (default ``http://127.0.0.1:8765/mcp``).

Requires the ``agent`` extra::

    uv sync --extra agent
    uv run pm-mcp &                       # serve the tools (binds a run via PM_RUN_ID)
    uv run pm-agent "Review the board and post a status update in #eng"

``LLMAgent`` itself is dependency-free (duck-typed model client + tool backend), so
the loop is unit-testable without a network or a running server; the OpenRouter and
MCP wiring lives in :func:`run_agent` / :func:`main`, which import their deps lazily.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Curated OpenRouter model ids the agent can be run with (see examples/run_agent_llm.py
# and tests/test_agent_llm.py). Any OpenRouter model id works; these are one flagship
# per vendor plus a couple of free (no-cost) models.
MODELS = [
    # one per vendor (paid) — each vendor's flagship / top tier
    "openai/gpt-5.5-pro",
    "anthropic/claude-opus-4.8",
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
    "keep the project moving. When you are done, reply with a short summary and no "
    "tool call."
)


class ToolBackend(Protocol):
    """What :class:`LLMAgent` needs from a tool source (satisfied by MCP)."""

    async def list_tools(self) -> list[dict[str, Any]]: ...
    async def call(self, name: str, args: dict[str, Any]) -> str: ...


class LLMAgent:
    """Runs the model↔tool loop over any OpenAI-compatible client + tool backend."""

    def __init__(
        self, client: Any, model: str, backend: ToolBackend,
        *, system: str = _SYSTEM, max_steps: int = 12,
    ) -> None:
        self.client = client
        self.model = model
        self.backend = backend
        self.system = system
        self.max_steps = max_steps

    async def run(self, goal: str) -> str:
        """Pursue ``goal`` with the tools; return the model's final text."""
        tools = await self.backend.list_tools()
        messages: list[Any] = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": goal},
        ]
        for _ in range(self.max_steps):
            resp = await self.client.chat.completions.create(
                model=self.model, messages=messages, tools=tools or None,
            )
            msg = resp.choices[0].message
            messages.append(msg.model_dump() if hasattr(msg, "model_dump") else msg)
            calls = getattr(msg, "tool_calls", None)
            if not calls:
                return msg.content or ""
            for tc in calls:
                args = json.loads(tc.function.arguments or "{}")
                result = await self.backend.call(tc.function.name, args)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
                )
        return "(reached max steps without a final answer)"


class McpBackend:
    """A :class:`ToolBackend` backed by a live MCP ``ClientSession``."""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def list_tools(self) -> list[dict[str, Any]]:
        listed = await self._session.list_tools()
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.inputSchema or {"type": "object", "properties": {}},
                },
            }
            for t in listed.tools
        ]

    async def call(self, name: str, args: dict[str, Any]) -> str:
        result = await self._session.call_tool(name, args)
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            return json.dumps(structured)
        parts = [
            block.text for block in getattr(result, "content", [])
            if getattr(block, "text", None)
        ]
        return "\n".join(parts) or "(no output)"


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


# The four AgentTools capabilities as OpenAI function schemas (see pm/agent/tools.py).
_AGENT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    _tool("send_slack", "Post a message to a Slack channel.",
          {"channel_id": {"type": "string"}, "body": {"type": "string"}},
          ["channel_id", "body"]),
    _tool("read_slack", "Read the messages in a Slack channel, oldest first.",
          {"channel_id": {"type": "string"}}, ["channel_id"]),
    _tool("read_jira_board", "Read a project's Jira board: its issues (each with an "
          "assignee_id) and a status breakdown.",
          {"project_id": {"type": "string"}}, ["project_id"]),
    _tool("read_calendar", "Read the meetings a person attends (default: the agent).",
          {"person_id": {"type": "string"}}, []),
]


class InProcessBackend:
    """A :class:`ToolBackend` that calls :class:`~pm.agent.tools.AgentTools` directly.

    Exposes the same four tools as the MCP server but with no server/transport — the
    simplest way to drive :class:`LLMAgent` in-process (e.g. from tests).
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


async def run_agent(goal: str, *, url: str, model: str, api_key: str) -> str:
    """Connect to the MCP tools server + OpenRouter and run the agent to ``goal``."""
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            agent = LLMAgent(client, model, McpBackend(session))
            return await agent.run(goal)


def main() -> None:
    """CLI entry (``pm-agent``): read config from .env/env and run the agent."""
    import asyncio
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    model = os.environ.get("OPENROUTER_MODEL")
    url = os.environ.get("PM_MCP_URL", "http://127.0.0.1:8765/mcp")
    if not api_key or not model:
        raise SystemExit(
            "Set OPENROUTER_API_KEY and OPENROUTER_MODEL in .env (see .env.example)."
        )
    goal = " ".join(sys.argv[1:]) or "Review the Jira board and summarize the status."
    print(asyncio.run(run_agent(goal, url=url, model=model, api_key=api_key)))


if __name__ == "__main__":
    main()
