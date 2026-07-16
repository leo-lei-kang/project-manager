"""The FastMCP server exposing AgentTools: tool listing + calling over MCP."""

from __future__ import annotations

import pytest

pytest.importorskip("mcp")  # the server needs the optional `mcp` extra

from mcp.server.fastmcp import FastMCP  # noqa: E402

from pm.agent.mcp_server import register_tools  # noqa: E402
from pm.agent.tools import AgentTools  # noqa: E402
from pm.env import Env  # noqa: E402
from pm.jira.api import JiraApi  # noqa: E402
from pm.jira.repository import JiraRepository  # noqa: E402
from pm.npc.cast import seed_cast  # noqa: E402
from pm.world.models import Project  # noqa: E402

_TOOL_NAMES = {"send_slack", "read_slack", "read_jira_board", "read_calendar"}


@pytest.fixture
def bound(tmp_path):
    """A live AgentTools over a seeded run, plus its env."""
    env = Env.make(run_id="mcp", root=tmp_path)
    seed_cast(env.store)
    env.store.add_project(Project(id="checkout", name="Checkout"))
    env.store.db.execute("INSERT INTO channel (id, name, kind) VALUES ('eng', 'eng', 'channel')")
    repo = JiraRepository(env.store)
    repo.ensure_schema()
    JiraApi(repo, env.engine).create_issue("checkout", "task", "API",
                                           estimate_minutes=60, actor="erin")
    yield AgentTools(env), env
    env.close()


@pytest.fixture
def server(bound):
    tools, _ = bound
    mcp = FastMCP("test-pm-agent")
    register_tools(mcp, lambda: tools)
    return mcp


async def test_list_tools_exposes_the_four_with_schemas(server) -> None:
    listed = await server.list_tools()
    by_name = {t.name: t for t in listed}
    assert set(by_name) == _TOOL_NAMES
    assert by_name["send_slack"].description  # docstring became the description
    props = by_name["send_slack"].inputSchema["properties"]  # signature became the schema
    assert "channel_id" in props and "body" in props


async def test_call_send_slack_posts_and_costs_time(server, bound) -> None:
    tools, env = bound
    assert env.clock.now() == 0
    await server.call_tool("send_slack", {"channel_id": "eng", "body": "hi team"})
    assert [m.body for m in env.store.list_messages("eng")] == ["hi team"]
    assert env.clock.now() == tools.send_cost  # the send consumed sim-time


async def test_call_read_jira_board_returns_the_board(server) -> None:
    result = await server.call_tool("read_jira_board", {"project_id": "checkout"})
    # FastMCP returns (content_blocks, structured_result); the structured payload
    # carries our dict either way — assert the seeded issue is present.
    assert "API" in str(result)
