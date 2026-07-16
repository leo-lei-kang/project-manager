"""An LLM agent that drives the PM tools with a Claude model — official Anthropic SDK.

Drop-in twin of :class:`pm.agent.openrouter_agent.LLMAgent` (same ``run(goal)``
contract, same tool backend, same agent-log entry shape, so ``AgentLog``, eval,
and viz work unchanged) with the loop implemented against the Anthropic Messages
API: model → ``tool_use`` blocks → backend call → ``tool_result`` until Claude
replies with no tool call. Uses adaptive thinking; thinking blocks are echoed
back unchanged on each turn, as the API requires.

Select it by model id: any ``claude-*`` model routes here (see
:func:`pm.agent.hook.llm_review_hook` and ``examples/run_agent_llm.py``);
needs ``ANTHROPIC_API_KEY`` in ``.env``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pm.agent.openrouter_agent import _SYSTEM, ToolBackend

DEFAULT_CLAUDE_MODEL = "claude-opus-4-8"


def _anthropic_tools(openai_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The backend's OpenAI-format tool schemas in Anthropic format."""
    return [
        {"name": t["function"]["name"],
         "description": t["function"]["description"],
         "input_schema": t["function"]["parameters"]}
        for t in openai_tools
    ]


class ClaudeAgent:
    """Runs the model↔tool loop over an ``AsyncAnthropic`` client + tool backend."""

    def __init__(
        self, client: Any, model: str, backend: ToolBackend,
        *, system: str = _SYSTEM, max_steps: int = 12,
        log: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.backend = backend
        self.system = system
        self.max_steps = max_steps
        self.log = log

    def _log(self, entry: dict[str, Any]) -> None:
        if self.log is not None:
            self.log(entry)

    async def run(self, goal: str) -> str:
        """Pursue ``goal`` with the tools; return the model's final text."""
        tools = _anthropic_tools(await self.backend.list_tools())
        messages: list[dict[str, Any]] = [{"role": "user", "content": goal}]
        sent_from = 0  # messages[sent_from:] = what this round-trip newly sent
        for step in range(self.max_steps):
            self._log({"kind": "llm_request", "step": step, "model": self.model,
                       "messages": len(messages)})
            resp = await self.client.messages.create(
                model=self.model, max_tokens=16000,
                thinking={"type": "adaptive"},
                system=self.system, tools=tools, messages=messages,
            )
            new_input = messages[sent_from:]
            # Append the full content (thinking blocks included, unchanged) —
            # dumped to plain dicts so the log mirror stays JSON-safe.
            messages.append({"role": "assistant",
                             "content": [b.model_dump() for b in resp.content]})
            sent_from = len(messages)
            calls = [b for b in resp.content if b.type == "tool_use"]
            text = "".join(b.text for b in resp.content if b.type == "text")
            self._log({
                "kind": "llm_call", "step": step, "model": self.model,
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
                "tool_calls": [c.name for c in calls],
                "input": new_input,
                "output": {"content": text or None,
                           "tool_calls": [{"name": c.name, "args": c.input}
                                          for c in calls]},
            })
            if resp.stop_reason != "tool_use":
                return text  # end_turn (or refusal/max_tokens: whatever text there is)
            results = []
            for c in calls:
                self._log({"kind": "tool_call", "name": c.name, "args": c.input})
                result = await self.backend.call(c.name, dict(c.input))
                results.append({"type": "tool_result", "tool_use_id": c.id,
                                "content": result})
            messages.append({"role": "user", "content": results})
        return "(reached max steps without a final answer)"
