"""Run the PM agent on a Claude model via the official Anthropic SDK — in-process.

    uv run python examples/run_claude_agent_llm.py [MODEL] [PROMPT ...]
    uv run python examples/run_claude_agent_llm.py --scenario single_engineer_with_agent [MODEL]

The Claude twin of ``examples/run_agent_llm.py`` (which drives OpenRouter):
default mode seeds the same throwaway ticket board and runs one
:class:`~pm.agent.claude_agent.ClaudeAgent` loop; ``--scenario NAME``
reproduces one in-sim PM review loop standalone on that scenario's board and
PROMPT, printing the wall-clock time of every model round-trip.

MODEL defaults to ``claude-opus-4-8``. Needs ``ANTHROPIC_API_KEY`` in ``.env``
(see ``.env.example``) — calls bill your Anthropic account.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

from pm.agent.claude_agent import DEFAULT_CLAUDE_MODEL, ClaudeAgent
from pm.agent.openrouter_agent import InProcessBackend
from pm.agent.tools import AgentTools
from run_agent_llm import (
    _DEFAULT_PROMPT,
    _RUN_ID,
    _banner,
    _log,
    _seed,
    _timed_log,
)

from pm.env import Env


def _make_agent(model: str, backend: InProcessBackend, **kwargs) -> ClaudeAgent:
    from anthropic import AsyncAnthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY in .env (see .env.example).")
    return ClaudeAgent(AsyncAnthropic(), model, backend, **kwargs)


def _run_in_process(model: str, prompt: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.monotonic()
        env = Env.make(run_id=_RUN_ID, root=Path(tmp))
        tools = _seed(env)
        _log(f"board seeded ({time.monotonic() - t0:.2f}s)")
        agent = _make_agent(model, InProcessBackend(tools), log=_timed_log())
        _banner(model, "in-process (Anthropic SDK, no server)", prompt)
        _log("agent loop starting")
        t0 = time.monotonic()
        answer = asyncio.run(agent.run(prompt))
        _log(f"answer: {answer}")
        _log(f"total: {time.monotonic() - t0:.1f}s for the loop")
        env.close()


def _run_scenario_review(name: str, model: str) -> None:
    """One in-sim review loop, standalone: the scenario's board, PROMPT, and tools."""
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
        # max_steps=6 mirrors pm.agent.hook.llm_review_hook's review budget.
        agent = _make_agent(model, InProcessBackend(AgentTools(env)),
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
    load_dotenv()

    if args and args[0] == "--scenario":
        if len(args) < 2:
            raise SystemExit("usage: run_claude_agent_llm.py --scenario NAME [MODEL]")
        _run_scenario_review(args[1], args[2] if len(args) > 2 else DEFAULT_CLAUDE_MODEL)
        return

    model = args[0] if args else DEFAULT_CLAUDE_MODEL
    prompt = " ".join(args[1:]) or _DEFAULT_PROMPT
    _run_in_process(model, prompt)


if __name__ == "__main__":
    main()
