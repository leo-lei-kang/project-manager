"""Run the PM agent against a seeded board, with a model you pick — in-process.

    uv run python examples/run_agent_llm.py [MODEL] [PROMPT ...]
    uv run python examples/run_agent_llm.py --scenario single_engineer_with_agent [MODEL]
    uv run python examples/run_agent_llm.py --list

Default mode seeds a throwaway world where one coworker clearly has the most
Jira tickets (alice ×3, bob ×1, clare ×1), then wires the tools straight to the
model via ``InProcessBackend`` (no server) and runs the loop.

``--scenario NAME`` instead reproduces ONE in-sim PM review loop standalone:
it builds that scenario's board (week-start state) and runs the scenario's own
PROMPT with the review hook's step budget, printing the wall-clock time of
every model round-trip — the way to measure why a `pm sim` week with the LLM
PM feels slow (it blocks on 2-3 such round-trips per review, ~30 reviews a
week) without simulating the week around it. MODEL overrides the scenario's
default.

MODEL defaults to the OpenAI flagship from ``pm.agent.openrouter_agent.MODELS``;
PROMPT defaults to "who has the most Jira tickets?". Needs ``OPENROUTER_API_KEY``
in ``.env`` (see ``.env.example``) — free models cost nothing, paid ones bill
your OpenRouter account.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime
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


def _log(msg: str) -> None:
    """Print ``msg`` stamped with wall time at millisecond precision."""
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}")


def _banner(model: str, backend: str, prompt: str) -> None:
    _log(f"model:   {model}")
    _log(f"backend: {backend}")
    _log(f"tickets: {_TICKETS}")
    _log(f"prompt:  {prompt}")


def _run_in_process(model: str, prompt: str, api_key: str) -> None:
    from openai import AsyncOpenAI

    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.monotonic()
        env = Env.make(run_id=_RUN_ID, root=Path(tmp))
        tools = _seed(env)
        _log(f"board seeded ({time.monotonic() - t0:.2f}s)")
        client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
        agent = LLMAgent(client, model, InProcessBackend(tools), log=_timed_log())
        _banner(model, "in-process (no server)", prompt)
        _log("agent loop starting")
        t0 = time.monotonic()
        answer = asyncio.run(agent.run(prompt))
        _log(f"answer: {answer}")
        _log(f"total: {time.monotonic() - t0:.1f}s for the loop")
        env.close()


def _timed_log() -> callable:
    """Print each agent-log entry, wall-time stamped, with the seconds since the
    previous entry (an llm_call's delta is its model round trip; the tools run
    locally in microseconds, so the deltas isolate the network)."""
    last = time.monotonic()

    def log(entry: dict) -> None:
        nonlocal last
        dt, last = time.monotonic() - last, time.monotonic()
        if entry["kind"] == "llm_request":
            _log(f"  llm_request step {entry['step']}: prompt sent "
                 f"({entry['messages']} messages, {len(entry['input'])} new)")
            for m in entry["input"]:
                print(json.dumps(m, indent=4))
        elif entry["kind"] == "llm_call":
            _log(f"  llm_call step {entry['step']}: {dt:6.2f}s — "
                 f"{entry['input_tokens']} in / {entry['output_tokens']} out")
            print(json.dumps(entry["output"], indent=4))
        else:
            _log(f"  tool_call {entry['name']} (+{dt:.3f}s)")

    return log


def _run_scenario_review(name: str, model: str | None, api_key: str) -> None:
    """One in-sim review loop, standalone: the scenario's board, PROMPT, and tools."""
    from openai import AsyncOpenAI

    from pm.cli import SCENARIOS

    if name not in SCENARIOS:
        raise SystemExit(f"unknown scenario {name!r} (choices: {', '.join(SCENARIOS)})")
    module = SCENARIOS[name]
    if not hasattr(module, "PROMPT"):
        raise SystemExit(f"scenario {name!r} has no PM review loop (no PROMPT)")

    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.monotonic()
        env = module.build(run_id=_RUN_ID, root=Path(tmp))
        _log(f"scenario board built ({time.monotonic() - t0:.2f}s)")
        model = model or getattr(module, "DEFAULT_MODEL", _DEFAULT_MODEL)
        client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
        # max_steps=6 mirrors pm.agent.hook.llm_review_hook's review budget.
        agent = LLMAgent(client, model, InProcessBackend(AgentTools(env)),
                         max_steps=6, log=_timed_log())
        _log(f"model:    {model}")
        _log(f"scenario: {name} (week-start board, one review loop)")
        t0 = time.monotonic()
        answer = asyncio.run(agent.run(module.PROMPT))
        _log(f"answer: {answer}")
        _log(f"total:  {time.monotonic() - t0:.1f}s for one review loop")
        env.close()


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in ("--list", "-l"):
        print("Available models (pm.agent.openrouter_agent.MODELS):")
        for m in MODELS:
            print(f"  {m}")
        return

    load_dotenv()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("Set OPENROUTER_API_KEY in .env (see .env.example).")

    if args and args[0] == "--scenario":
        if len(args) < 2:
            raise SystemExit("usage: run_agent_llm.py --scenario NAME [MODEL]")
        _run_scenario_review(args[1], args[2] if len(args) > 2 else None, api_key)
        return

    model = args[0] if args else _DEFAULT_MODEL
    prompt = " ".join(args[1:]) or _DEFAULT_PROMPT
    _run_in_process(model, prompt, api_key)


if __name__ == "__main__":
    main()
