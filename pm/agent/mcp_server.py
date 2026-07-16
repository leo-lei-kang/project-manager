"""MCP server exposing the PM agent's tools — mirrors fleet-sdk's ``mcp_server``.

Serves the four :class:`~pm.agent.tools.AgentTools` capabilities over MCP so an
LLM / MCP client can ``list_tools`` and ``call_tool`` them against a live run. As in
fleet, each tool is a ``@mcp.tool()`` function — **name = function name, description
= docstring, input schema = the typed signature** — and the live target is injected
through a ``get_tools`` accessor (fleet uses ``get_computer``) bound in the async
``lifespan`` to the run named by ``PM_RUN_ID``.

Requires the ``mcp`` extra::

    uv sync --extra mcp
    uv run pm-mcp            # serves over streamable-http (PM_MCP_HOST/PM_MCP_PORT)
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pm.agent.tools import AgentTools
from pm.env.environment import RUNS_DIR, Env

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

_tools: AgentTools | None = None


def _get_tools() -> AgentTools:
    if _tools is None:
        raise RuntimeError("AgentTools unavailable — the server lifespan is not active")
    return _tools


def register_tools(mcp: FastMCP, get_tools: Callable[[], AgentTools]) -> None:
    """Register the agent's four tools on ``mcp`` (mirrors fleet's register_tools).

    ``get_tools`` is injected so the tools resolve the same live ``AgentTools`` on
    every call — and so this is unit-testable without a running server.
    """

    @mcp.tool()
    def send_slack(channel_id: str, body: str) -> dict[str, Any]:
        """Post a message to a Slack channel (consumes simulated time).

        Args:
            channel_id: The channel to post to.
            body: The message text to send.
        """
        return get_tools().send_slack(channel_id, body)

    @mcp.tool()
    def read_slack(channel_id: str) -> list[dict[str, Any]]:
        """Read the messages in a Slack channel, oldest first.

        Args:
            channel_id: The channel to read.
        """
        return get_tools().read_slack(channel_id)

    @mcp.tool()
    def read_jira_board(project_id: str) -> dict[str, Any]:
        """Read a project's Jira board: its issues and a status breakdown.

        Args:
            project_id: The project whose board to read.
        """
        return get_tools().read_jira_board(project_id)

    @mcp.tool()
    def read_calendar(person_id: str | None = None) -> list[dict[str, Any]]:
        """Read the meetings a person attends (defaults to the agent).

        Args:
            person_id: Whose calendar to read; omit for the agent's own.
        """
        return get_tools().read_calendar(person_id)


@asynccontextmanager
async def lifespan(_app: FastMCP) -> AsyncIterator[None]:
    """Bind ``AgentTools`` to the run named by ``PM_RUN_ID`` for the server's life."""
    global _tools
    run_id = os.environ.get("PM_RUN_ID", "demo")
    root = Path(os.environ.get("PM_RUNS_ROOT", str(RUNS_DIR)))
    env = Env.load(run_id, root=root)
    _tools = AgentTools(env)
    try:
        yield
    finally:
        _tools = None
        env.close()


def build_server() -> "FastMCP":
    """Construct the tools MCP server (deferred so importing this module needs no mcp)."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "pm-agent",
        lifespan=lifespan,
        host=os.environ.get("PM_MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("PM_MCP_PORT", "8765")),
    )
    register_tools(mcp, _get_tools)
    return mcp


def main() -> None:
    """Serve the tools over MCP (streamable-http), like fleet's mcp_server."""
    build_server().run(transport="streamable-http")


if __name__ == "__main__":
    main()
