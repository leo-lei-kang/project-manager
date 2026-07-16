"""`RemoteMCP` — provider-native descriptors for the remote (provider-side) path.

Hermetic: only checks the descriptor shapes and URL defaulting; `list_tools`
needs a live server and is exercised by `examples/run_agent_llm.py --remote`.
"""

from __future__ import annotations

from pm.agent import RemoteMCP, remote_mcp
from pm.agent.mcp_resource import DEFAULT_MCP_URL

_URL = "http://example.test:8765/mcp"


def test_openai_descriptor() -> None:
    assert RemoteMCP(_URL, name="pm-agent").openai() == {
        "type": "mcp",
        "server_label": "pm-agent",
        "server_url": _URL,
        "require_approval": "never",
    }


def test_anthropic_descriptor() -> None:
    assert RemoteMCP(_URL, name="pm-agent").anthropic() == {
        "type": "url",
        "url": _URL,
        "name": "pm-agent",
    }


def test_remote_mcp_defaults_url_from_env(monkeypatch) -> None:
    monkeypatch.delenv("PM_MCP_URL", raising=False)
    assert remote_mcp().url == DEFAULT_MCP_URL

    monkeypatch.setenv("PM_MCP_URL", _URL)
    assert remote_mcp().url == _URL
