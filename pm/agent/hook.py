"""The in-sim LLM review hook — the PM agent acting *inside* a simulated week.

:func:`llm_review_hook` builds a per-tick hook (the ``agent_review_hook``
contract composed by :func:`pm.scenarios.runner.drive`) that runs one
:class:`~pm.agent.openrouter_agent.LLMAgent` loop over the agent's tools,
bound to the run's ``Env``, whenever a trigger fires:

  * **cadence** — every ``period`` ticks (default once per workday, at 09:00),
  * **meeting_end** — a meeting completing (its transcript just appeared),
  * **slack** — someone else's Slack message naming the agent landing (e.g.
    the CxO's daily "PM, status update?" push).

Each firing logs an ``agent.review.trigger`` event with its reason. Two
run-integration rules:

  * **Clock-safe sends.** Inside the sim loop, ``AgentTools.send_slack`` would
    advance the clock mid-tick (it routes through ``perform_action``), so the
    hook's tools override it to schedule a :class:`~pm.sim.events.SlackSendEvent`
    at the current tick instead.
  * **Everything is logged.** Each model round-trip (with token usage) and each
    tool call is appended, stamped with the sim tick, to the run's
    ``agent-<model>.jsonl`` (see :mod:`pm.agent.log`) — the source for eval's
    token totals and viz's activity timeline — and mirrored into the run's
    ``event_log`` as ``agent.llm_call`` / ``agent.tool_call`` rows.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pm.agent.log import AgentLog, agent_log_name
from pm.agent.openrouter_agent import (
    OPENROUTER_BASE_URL,
    InProcessBackend,
    LLMAgent,
)
from pm.agent.tools import AgentTools
from pm.env.environment import Env
from pm.exceptions import ConfigurationError
from pm.sim.clock import MINUTES_PER_WORKDAY
from pm.sim.events import EventType, SlackSendEvent

if TYPE_CHECKING:
    from pm.sim.simulation import Simulation


class _HookSafeTools(AgentTools):
    """AgentTools whose ``send_slack`` is safe inside the sim's tick loop.

    Schedules a ``SlackSendEvent`` at the current tick rather than routing
    through ``perform_action``, which would advance the clock mid-loop.
    """

    def __init__(self, env: Env) -> None:
        super().__init__(env)
        self._sent = 0

    def send_slack(self, channel_id: str, body: str) -> dict[str, Any]:
        now = self.env.clock.now()
        self._sent += 1
        message_id = f"{self.actor}-llm-{now}-{self._sent}"
        self.env.engine.schedule(SlackSendEvent(
            owner_id=self.actor, start_tick=now,
            payload={"message_id": message_id, "channel_id": channel_id, "body": body},
        ))
        return {"id": message_id, "channel_id": channel_id, "body": body,
                "sent_tick": now, "scheduled": True}


def _default_client() -> Any:
    """An OpenRouter ``AsyncOpenAI`` client from ``.env`` (lazy imports)."""
    from dotenv import load_dotenv
    from openai import AsyncOpenAI

    load_dotenv()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ConfigurationError(
            "OPENROUTER_API_KEY is not set; the LLM review hook needs it "
            "(see .env.example)"
        )
    return AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)


def llm_review_hook(
    env: Env, *, model: str, prompt: str, client: Any = None,
    period: int = MINUTES_PER_WORKDAY, max_steps: int = 6,
) -> Callable[["Simulation"], None]:
    """Build a hook that runs the LLM agent whenever a review trigger fires.

    Triggers, checked each tick: the ``period`` cadence (default one review per
    workday, at 09:00); a meeting ending (its transcript becomes available at
    that moment, so the agent reads it right away); and a Slack message from
    someone else naming the agent completing (the CxO's daily status push).
    Each firing logs an ``agent.review.trigger`` event with its ``reason``.

    ``client`` is any OpenAI-compatible async client (tests inject a fake);
    ``None`` builds the OpenRouter client from ``.env`` on first use. Every entry
    lands twice: in the run's ``agent-<model>.jsonl`` (eval/viz source) and,
    prefixed ``agent.``, in the unified ``event_log`` timeline.
    """
    tools = _HookSafeTools(env)
    backend = InProcessBackend(tools)
    log = AgentLog(env.root / env.run_id / agent_log_name(model))
    agent_person = env.store.get_person(tools.actor)

    def _names_agent(body: str) -> bool:
        # Mirrors pm.sim.npc._named: case-insensitive substring of name or id.
        low = body.lower()
        if agent_person is not None and agent_person.name.lower() in low:
            return True
        return tools.actor.lower() in low

    def stamped(entry: dict[str, Any]) -> None:
        tick = env.clock.now()
        log.append({"tick": tick, **entry})
        env.store.log_event(tick, actor="agent",
                            kind=f"agent.{entry['kind']}", payload=entry)

    def hook(sim: "Simulation") -> None:
        nonlocal client
        now = sim.clock.now()
        done = sim.store.events_done_at(now)
        if now % period == 0:
            reason = "cadence"
        elif any(e.type is EventType.MEETING for e in done):
            reason = "meeting_end"
        elif any(e.type is EventType.SLACK_SEND and e.owner_id != tools.actor
                 and _names_agent(e.payload.get("body", "")) for e in done):
            reason = "slack"
        else:
            return
        sim.store.log_event(now, actor="agent", kind="agent.review.trigger",
                            payload={"reason": reason})
        if client is None:
            client = _default_client()
        agent = LLMAgent(client, model, backend, max_steps=max_steps, log=stamped)
        goal = prompt
        memory = tools.memory_text()
        if memory:
            goal += f"\n\n## Your memory from earlier reviews\n{memory}"
        asyncio.run(agent.run(goal))

    return hook
