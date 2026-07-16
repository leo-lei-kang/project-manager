"""Point a provider (or any MCP client) straight at a running tools MCP server.

Mirrors fleet-sdk's ``SyncMCPResource`` (``fleet/resources/mcp.py``): given the URL
of a running ``pm-mcp`` server, produce **provider-native MCP descriptors** so an
OpenAI / Anthropic model can execute the tools *server-side* — the "remote MCP"
path — with no client-side loop of our own.

This is the counterpart to the local loop in :mod:`pm.agent.openrouter_agent`
(:class:`~pm.agent.openrouter_agent.LLMAgent` + ``McpBackend`` / ``InProcessBackend``,
which drives ``model → tool_calls → call_tool`` here). Pick by where tool execution
should happen:

  * **remote** — hand :meth:`RemoteMCP.openai` / :meth:`RemoteMCP.anthropic` to a
    provider that runs MCP itself (OpenAI Responses API, Anthropic MCP connector);
    needs a URL the provider can reach (a public/tunnelled server, not localhost).
  * **local**  — run :func:`pm.agent.openrouter_agent.run_agent`; works with any
    OpenAI-compatible chat endpoint (e.g. OpenRouter) and a localhost server.
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_MCP_URL = "http://127.0.0.1:8765/mcp"


class RemoteMCP:
    """A running tools MCP server, described for provider-side (remote) use."""

    def __init__(self, url: str, *, name: str = "pm-agent") -> None:
        self.url = url
        self.name = name

    def openai(self) -> dict[str, Any]:
        """Descriptor for the OpenAI Responses API ``tools`` list (``type: mcp``)."""
        return {
            "type": "mcp",
            "server_label": self.name,
            "server_url": self.url,
            "require_approval": "never",
        }

    def anthropic(self) -> dict[str, Any]:
        """Descriptor for the Anthropic Messages ``mcp_servers`` list (``type: url``)."""
        return {
            "type": "url",
            "url": self.url,
            "name": self.name,
        }

    async def list_tools(self) -> list[dict[str, Any]]:
        """List the server's tools (``name`` / ``description`` / ``input_schema``).

        Does the real MCP handshake over streamable-http (unlike fleet's raw JSON-RPC
        POST), so it doubles as a liveness check for the server.
        """
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(self.url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema or {"type": "object", "properties": {}},
            }
            for t in listed.tools
        ]


def remote_mcp(url: str | None = None, *, name: str = "pm-agent") -> RemoteMCP:
    """A :class:`RemoteMCP` for ``url`` (default: ``$PM_MCP_URL`` or localhost:8765)."""
    return RemoteMCP(url or os.environ.get("PM_MCP_URL", DEFAULT_MCP_URL), name=name)
