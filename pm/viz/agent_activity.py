"""The LLM agent's activity over the week, from ``runs/<run_id>/agent-*.jsonl``.

:func:`write_agent_activity` reads the run's agent logs (see :mod:`pm.agent.log`)
and writes ``runs/<run_id>/agent_activity.html``: a Mon–Fri strip with one
marker per logged entry — model round-trips (with token usage in the tooltip)
on one lane, tool calls on another — plus the entries as a list. Returns
``None`` when the run has no agent log (agent-less scenarios). Zero
dependencies beyond the project itself.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from pm.agent.log import read_agent_logs
from pm.env.environment import RUNS_DIR
from pm.sim.clock import MINUTES_PER_WORKDAY, TICKS_PER_WEEK, WEEKDAYS, format_tick

PX_PER_TICK = 0.5
PAD = 16
GUTTER_W = 70  # lane labels
HEADER_H = 20  # weekday names
LANE_H = 28
LANES = (("llm_call", "LLM calls", "#256abf"), ("tool_call", "Tool calls", "#008300"))


def _describe(entry: dict[str, Any]) -> str:
    if entry.get("kind") == "llm_call":
        called = ", ".join(entry.get("tool_calls", [])) or "no tool call"
        return (f"{entry.get('model', '?')} — {entry.get('input_tokens', 0)} in / "
                f"{entry.get('output_tokens', 0)} out tokens — {called}")
    return f"{entry.get('name', '?')} {entry.get('args', {})}"


def _svg_markers(entries: list[dict[str, Any]]) -> list[str]:
    lane_index = {kind: i for i, (kind, _, _) in enumerate(LANES)}
    color = {kind: c for kind, _, c in LANES}
    out = []
    for entry in entries:
        kind = entry.get("kind", "")
        if kind not in lane_index:
            continue
        tick = int(entry.get("tick", 0))
        cx = PAD + GUTTER_W + tick * PX_PER_TICK
        cy = PAD + HEADER_H + lane_index[kind] * LANE_H + LANE_H / 2
        tooltip = f"{format_tick(tick)} — {_describe(entry)}"
        out.append(
            f'<g><title>{html.escape(tooltip)}</title>'
            f'<circle cx="{cx:g}" cy="{cy:g}" r="5" fill="{color[kind]}"'
            f' fill-opacity="0.75"/></g>'
        )
    return out


def render_agent_activity_svg(entries: list[dict[str, Any]]) -> str:
    """The week strip: day grid + one lane per entry kind + a marker per entry."""
    width = 2 * PAD + GUTTER_W + TICKS_PER_WEEK * PX_PER_TICK
    lanes_h = len(LANES) * LANE_H
    height = 2 * PAD + HEADER_H + lanes_h
    grid_top, grid_bottom = PAD + HEADER_H, PAD + HEADER_H + lanes_h

    parts = [
        f'<svg width="{width:g}" height="{height}" xmlns="http://www.w3.org/2000/svg"'
        f' font-family="system-ui, sans-serif">'
    ]
    for day in range(len(WEEKDAYS) + 1):
        x = PAD + GUTTER_W + day * MINUTES_PER_WORKDAY * PX_PER_TICK
        parts.append(
            f'<line x1="{x:g}" y1="{grid_top}" x2="{x:g}" y2="{grid_bottom}"'
            f' stroke="#e1e0d9"/>'
        )
    for day, name in enumerate(WEEKDAYS):
        cx = PAD + GUTTER_W + (day + 0.5) * MINUTES_PER_WORKDAY * PX_PER_TICK
        parts.append(
            f'<text x="{cx:g}" y="{PAD + 12}" font-size="11" text-anchor="middle"'
            f' fill="#52514e">{name}</text>'
        )
    for i, (_, label, _) in enumerate(LANES):
        y = PAD + HEADER_H + i * LANE_H + LANE_H / 2 + 4
        parts.append(
            f'<text x="{PAD}" y="{y:g}" font-size="12" font-weight="600"'
            f' fill="#0b0b0b">{label}</text>'
        )
    parts += _svg_markers(entries)
    parts.append("</svg>")
    return "\n".join(parts)


def render_agent_activity_html(entries: list[dict[str, Any]], heading: str) -> str:
    llm_calls = [e for e in entries if e.get("kind") == "llm_call"]
    tokens_in = sum(e.get("input_tokens", 0) for e in llm_calls)
    tokens_out = sum(e.get("output_tokens", 0) for e in llm_calls)
    items = "\n".join(
        f"<li><strong>{html.escape(format_tick(int(e.get('tick', 0))))}</strong>"
        f" — {html.escape(e.get('kind', '?'))} — {html.escape(_describe(e))}</li>"
        for e in entries
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Agent activity — {html.escape(heading)}</title>
<style>
  body {{ font: 13px system-ui, sans-serif; margin: 24px; background: #f9f9f7; color: #0b0b0b; }}
  h1 {{ font-size: 18px; }} h2 {{ font-size: 15px; margin-top: 28px; }}
  .graph {{ overflow-x: auto; background: #fcfcfb; border: 1px solid #e1e0d9;
            border-radius: 6px; padding: 8px; }}
  ul {{ line-height: 1.7; }}
  .hint {{ color: #898781; }}
</style></head><body>
<h1>Agent activity on the week timeline — {html.escape(heading)}</h1>
<p class="hint">One marker per logged agent entry over the Mon–Fri 09:00–17:00
work week; hover a marker for the model, token usage, or tool call.
Totals: {len(llm_calls)} LLM calls, {tokens_in} in / {tokens_out} out tokens.</p>
<div class="graph">
{render_agent_activity_svg(entries)}
</div>
<h2>Log entries</h2>
<ul>
{items}
</ul>
</body></html>
"""


def write_agent_activity(run_id: str, root: Path = RUNS_DIR) -> Path | None:
    """Render ``run_id``'s agent timeline to ``runs/<id>/agent_activity.html``.

    Returns ``None`` when the run has no ``agent-*.jsonl`` (agent-less scenarios).
    """
    run_dir = root / run_id
    entries = read_agent_logs(run_dir)
    if not entries:
        return None
    page = render_agent_activity_html(entries, heading=f"run {run_id}")
    out = run_dir / "agent_activity.html"
    out.write_text(page, encoding="utf-8")
    return out
