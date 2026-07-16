"""Render ``event_log`` entries as one-line sim-time narration.

The shared formatter behind `pm sim`'s live console stream and `pm log`:
``"Wed 14:30  alice    jira_ticket.done         MT-3"`` — tick as calendar
time, actor, kind, and the most telling payload detail.
"""

from __future__ import annotations

from pm.sim.clock import format_tick
from pm.world.models import LogEntry

_DETAIL_KEYS = ("issue_key", "title", "name", "body", "doc_id", "type")


def format_entry(e: LogEntry) -> str:
    """One narration line for a log entry, sim-time first."""
    p = e.payload
    if e.kind == "agent.llm_call":
        detail = (f"{p.get('model', '')} — {p.get('input_tokens', 0)} in / "
                  f"{p.get('output_tokens', 0)} out tokens")
    else:
        detail = next((str(p[k]) for k in _DETAIL_KEYS if p.get(k)), "")
    return f"{format_tick(e.sim_tick):>10}  {e.actor:<8} {e.kind:<24} {detail}".rstrip()
