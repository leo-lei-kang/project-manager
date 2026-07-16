"""Run the PM agent against a seeded board, with a model you pick — in-process.

    uv run python examples/run_agent_llm.py [MODEL] [PROMPT ...]
    uv run python examples/run_agent_llm.py --list

Seeds a throwaway world where one coworker clearly has the most Jira tickets
(alice ×3, bob ×1, clare ×1), then wires the tools straight to the model via
``InProcessBackend`` (no server) and runs the loop.

MODEL defaults to the OpenAI flagship from ``pm.agent.openrouter_agent.MODELS``;
PROMPT defaults to "who has the most Jira tickets?". Needs ``OPENROUTER_API_KEY``
in ``.env`` (see ``.env.example``) — free models cost nothing, paid ones bill
your OpenRouter account.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from pm.agent.openrouter_agent import (
    MODELS,
    OPENROUTER_BASE_URL,
    InProcessBackend,
    LLMAgent,
)
from pm.agent.tools import AgentTools
from pm.env import Env
from pm.npc.cast import seed_cast
from pm.world.models import Project

_RUN_ID = "run-agent-llm"
_DEFAULT_MODEL = "openai/gpt-5.5-pro"
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


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in ("--list", "-l"):
        print("Available models (pm.agent.openrouter_agent.MODELS):")
        for m in MODELS:
            print(f"  {m}")
        return

    model = args[0] if args else _DEFAULT_MODEL
    prompt = " ".join(args[1:]) or _DEFAULT_PROMPT

    load_dotenv()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("Set OPENROUTER_API_KEY in .env (see .env.example).")

    _run_in_process(model, prompt, api_key)


if __name__ == "__main__":
    main()
