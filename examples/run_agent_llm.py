"""Run the PM agent against a seeded board, with a model you pick — in-process,
over a real MCP server, or as provider-side (remote) MCP descriptors.

    uv run --extra agent python examples/run_agent_llm.py [MODEL] [PROMPT ...]
    uv run --extra agent python examples/run_agent_llm.py --mcp [MODEL] [PROMPT ...]
    uv run --extra agent python examples/run_agent_llm.py --remote
    uv run --extra agent python examples/run_agent_llm.py --list

Seeds a throwaway world where one coworker clearly has the most Jira tickets
(alice ×3, bob ×1, clare ×1).

  * default     — local loop: wires the tools straight to the model via
                  ``InProcessBackend`` (no server).
  * ``--mcp``   — local loop over a real server: self-hosts the tools MCP server
                  (``pm.agent.mcp_server`` as a subprocess bound to the seeded run)
                  and drives it through the MCP client (``run_agent`` →
                  ``McpBackend``), then tears the server down.
  * ``--remote``— the provider-side path: self-hosts the server and prints the
                  ``RemoteMCP`` descriptors a provider (OpenAI Responses / Anthropic
                  MCP connector) would consume, plus the server's live tool list. No
                  local loop and no LLM call — the provider would run the tools.

MODEL defaults to a free one from ``pm.agent.openrouter_agent.MODELS``; PROMPT
defaults to "who has the most Jira tickets?". The loop modes need ``OPENROUTER_API_KEY``
in ``.env`` (see ``.env.example``) — free models cost nothing, paid ones bill your
OpenRouter account; ``--remote`` needs no key.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv

from pm.agent.mcp_resource import remote_mcp
from pm.agent.openrouter_agent import (
    MODELS,
    OPENROUTER_BASE_URL,
    InProcessBackend,
    LLMAgent,
    run_agent,
)
from pm.agent.tools import AgentTools
from pm.env import Env
from pm.npc.cast import seed_cast
from pm.world.models import Project

_RUN_ID = "run-agent-llm"
_DEFAULT_MODEL = "openai/gpt-oss-20b:free"
_DEFAULT_PROMPT = (
    "Which coworker is assigned the most Jira tickets on the 'checkout' board? "
    "Reply with just their first name."
)
_TICKETS = {"alice": 3, "bob": 1, "clare": 1}


def _seed(env: Env) -> AgentTools:
    seed_cast(env.store)
    env.store.add_project(Project(id="checkout", name="Checkout"))
    tools = AgentTools(env)
    for who, count in _TICKETS.items():
        for i in range(count):
            tools.jira.create_issue("checkout", "task", f"{who}-task-{i}",
                                    estimate_minutes=60, assignee=who, actor="erin")
    return tools


def _banner(model: str, backend: str, prompt: str) -> None:
    print(f"model:   {model}")
    print(f"backend: {backend}")
    print(f"tickets: {_TICKETS}")
    print(f"prompt:  {prompt}\n")


def _run_in_process(model: str, prompt: str, api_key: str) -> None:
    from openai import AsyncOpenAI

    with tempfile.TemporaryDirectory() as tmp:
        env = Env.make(run_id=_RUN_ID, root=Path(tmp))
        tools = _seed(env)
        client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
        agent = LLMAgent(client, model, InProcessBackend(tools))
        _banner(model, "in-process (no server)", prompt)
        print("answer:", asyncio.run(agent.run(prompt)))
        env.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_server(proc: subprocess.Popen, host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SystemExit(
                f"MCP server exited early (code {proc.returncode}); "
                "is the 'agent' extra installed? (uv sync --extra agent)"
            )
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.25)
    raise SystemExit(f"MCP server not listening on {host}:{port} after {timeout}s")


@contextmanager
def _serve(host: str = "127.0.0.1") -> Iterator[str]:
    """Self-host the tools MCP server over a seeded run; yield its URL, tear it down."""
    with tempfile.TemporaryDirectory() as tmp:
        env = Env.make(run_id=_RUN_ID, root=Path(tmp))
        _seed(env)
        env.close()  # release world.db so the server subprocess can open it

        port = _free_port()
        proc = subprocess.Popen(
            [sys.executable, "-m", "pm.agent.mcp_server"],
            env={
                **os.environ,
                "PM_RUN_ID": _RUN_ID,
                "PM_RUNS_ROOT": tmp,
                "PM_MCP_HOST": host,
                "PM_MCP_PORT": str(port),
            },
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_server(proc, host, port, timeout=20)
            time.sleep(1.0)  # let the ASGI app finish mounting /mcp
            yield f"http://{host}:{port}/mcp"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def _run_over_mcp(model: str, prompt: str, api_key: str) -> None:
    with _serve() as url:
        _banner(model, f"MCP server (subprocess) at {url}", prompt)
        answer = asyncio.run(run_agent(prompt, url=url, model=model, api_key=api_key))
        print("answer:", answer)


def _run_remote() -> None:
    """Show the provider-side (remote) MCP surface against a live server."""
    import json

    with _serve() as url:
        res = remote_mcp(url)
        print(f"backend: remote MCP descriptors for {url}")
        print(f"tickets: {_TICKETS}\n")
        print("openai (Responses API tools[]):")
        print(json.dumps(res.openai(), indent=2))
        print("\nanthropic (Messages mcp_servers[]):")
        print(json.dumps(res.anthropic(), indent=2))
        tools = asyncio.run(res.list_tools())
        print("\nlive tools:", ", ".join(t["name"] for t in tools))
        print(
            "\nHand a descriptor to a provider that runs MCP server-side (against a "
            "URL it can reach) — it executes the tools; no local loop runs."
        )


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in ("--list", "-l"):
        print("Available models (pm.agent.openrouter_agent.MODELS):")
        for m in MODELS:
            print(f"  {m}")
        return

    if args and args[0] == "--remote":
        _run_remote()
        return

    use_mcp = bool(args) and args[0] == "--mcp"
    if use_mcp:
        args = args[1:]
    model = args[0] if args else _DEFAULT_MODEL
    prompt = " ".join(args[1:]) or _DEFAULT_PROMPT

    load_dotenv()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("Set OPENROUTER_API_KEY in .env (see .env.example).")

    (_run_over_mcp if use_mcp else _run_in_process)(model, prompt, api_key)


if __name__ == "__main__":
    main()
