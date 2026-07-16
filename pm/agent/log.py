"""The agent's JSONL activity log — one line per LLM round-trip or tool call.

Lives at ``runs/<run_id>/agent-<model>.jsonl`` next to ``eval.json`` (one file
per driving model, so runs re-driven with a different model keep separate
logs). Each entry is a plain dict stamped with the sim ``tick`` it happened
at; ``llm_request`` entries mark the moment a prompt goes out and carry
``input`` (the messages newly sent: the full prompt on step 0, tool results
after); ``llm_call`` entries mark the response arriving and carry the model
id, token usage (``input_tokens`` / ``output_tokens``), and ``output`` (the
reply's content and tool calls); ``tool_call`` entries the tool name and
arguments. Eval sums the tokens across all the run's agent logs; viz plots
the ticks.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def agent_log_name(model: str) -> str:
    """The run-dir filename for ``model``'s log, e.g. ``agent-openai-gpt-5.5-pro.jsonl``."""
    return f"agent-{re.sub(r'[^A-Za-z0-9._-]', '-', model)}.jsonl"


class AgentLog:
    """Append-only JSONL writer for one run's agent activity."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, entry: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def read_agent_log(path: Path) -> list[dict[str, Any]]:
    """One log's entries in order; ``[]`` when the file does not exist."""
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_agent_logs(run_dir: Path) -> list[dict[str, Any]]:
    """Every agent log entry in the run dir (all ``agent-*.jsonl``), tick order."""
    entries: list[dict[str, Any]] = []
    for path in sorted(run_dir.glob("agent-*.jsonl")):
        entries.extend(read_agent_log(path))
    return sorted(entries, key=lambda e: e.get("tick", 0))
