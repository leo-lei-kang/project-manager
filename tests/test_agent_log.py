"""The agent's JSONL log: LLMAgent emits llm_call/tool_call entries; the file round-trips."""

from __future__ import annotations

from pm.agent.log import AgentLog, agent_log_name, read_agent_log, read_agent_logs
from pm.agent.openrouter_agent import LLMAgent

from tests.llm_fakes import FakeClient, text_resp, tool_call_resp


class _RecordingBackend:
    async def list_tools(self) -> list[dict]:
        return [{"type": "function", "function": {
            "name": "read_jira_board", "description": "read the board",
            "parameters": {"type": "object", "properties": {}}}}]

    async def call(self, name: str, args: dict) -> str:
        return "{}"


async def test_llm_agent_logs_round_trips_and_tool_calls() -> None:
    client = FakeClient([
        tool_call_resp("read_jira_board", {"project_id": "checkout"},
                       prompt_tokens=111, completion_tokens=22),
        text_resp("all done", prompt_tokens=333, completion_tokens=44),
    ])
    entries: list[dict] = []
    agent = LLMAgent(client, "fake/model", _RecordingBackend(),
                     max_steps=4, log=entries.append)

    await agent.run("check the board")

    assert [e["kind"] for e in entries] == ["llm_call", "tool_call", "llm_call"]
    first, tool, last = entries
    assert first == {"kind": "llm_call", "step": 0, "model": "fake/model",
                     "input_tokens": 111, "output_tokens": 22,
                     "tool_calls": ["read_jira_board"]}
    assert tool == {"kind": "tool_call", "name": "read_jira_board",
                    "args": {"project_id": "checkout"}}
    assert last["input_tokens"] == 333 and last["output_tokens"] == 44
    assert last["tool_calls"] == []


async def test_llm_agent_without_log_still_runs() -> None:
    client = FakeClient([text_resp("nothing to do")])
    assert await LLMAgent(client, "fake/model", _RecordingBackend()).run("?") == "nothing to do"


def test_agent_log_round_trip(tmp_path) -> None:
    path = tmp_path / "agent.jsonl"
    assert read_agent_log(path) == []  # missing file reads as empty

    log = AgentLog(path)
    log.append({"tick": 0, "kind": "llm_call", "input_tokens": 1, "output_tokens": 2})
    log.append({"tick": 240, "kind": "tool_call", "name": "send_slack"})
    assert read_agent_log(path) == [
        {"tick": 0, "kind": "llm_call", "input_tokens": 1, "output_tokens": 2},
        {"tick": 240, "kind": "tool_call", "name": "send_slack"},
    ]


def test_agent_log_name_sanitizes_model_ids() -> None:
    assert agent_log_name("openai/gpt-oss-20b:free") == "agent-openai-gpt-oss-20b-free.jsonl"
    assert agent_log_name("openai/gpt-5.5-pro") == "agent-openai-gpt-5.5-pro.jsonl"


def test_read_agent_logs_merges_per_model_files_in_tick_order(tmp_path) -> None:
    AgentLog(tmp_path / agent_log_name("a/1")).append({"tick": 240, "kind": "llm_call"})
    AgentLog(tmp_path / agent_log_name("b/2")).append({"tick": 0, "kind": "llm_call"})
    assert [e["tick"] for e in read_agent_logs(tmp_path)] == [0, 240]
    assert read_agent_logs(tmp_path / "nowhere") == []
