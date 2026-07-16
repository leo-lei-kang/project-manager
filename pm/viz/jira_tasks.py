"""Jira tickets on the work-week timeline, rendered to static HTML/SVG.

Reads the additive ``issue``/``issue_dependency`` tables from a run's
``runs/<run_id>/world.db`` via :class:`JiraRepository` (read-only — no
``Engine`` needed) and renders a Gantt-style timeline over the 5-day × 8-hour
week (2400 ticks, Mon 09:00 → Fri 17:00): one swimlane per person, one bar per
ticket spanning the interval it was actually worked, dependency arrows from
blocker to dependent, plus a numbered completion order from a deterministic
topological sort. :func:`write_jira_tasks` writes the page to
``runs/<run_id>/jira_tasks.html``. Zero dependencies beyond the project itself.

Worked intervals come from the run's ``jira_ticket`` **event rows** (resolved
``start_tick`` + ``duration``), the same source the calendars renderer reads —
occupancy is freed on completion, so a finished run's history lives on the
events. A seed-state run has no work events yet: its lanes render empty and
every task lists under "Not scheduled".
"""

from __future__ import annotations

import heapq
import html
import json
import sqlite3
from pathlib import Path

from pm.db.store import Store
from pm.env.environment import RUNS_DIR, Env
from pm.exceptions import ConfigurationError
from pm.jira.format import format_hours
from pm.jira.models import Issue
from pm.jira.repository import JiraRepository
from pm.sim.clock import MINUTES_PER_WORKDAY, TICKS_PER_WEEK, WEEKDAYS, format_tick

# Timeline geometry (px). Half a pixel per tick: a 480-tick day is 240px wide.
PX_PER_TICK = 0.5
GUTTER_W, HEADER_H, LANE_H, LANE_GAP, PAD = 70, 20, 34, 8, 16
BAR_INSET = 4  # bar is inset vertically within its lane
MIN_LABEL_W = 40  # only bars at least this wide get an inline key label

# Status accent colors (dataviz status/categorical tokens); the status word is
# always in the tooltip so color never carries the state alone.
STATUS_COLOR = {
    "todo": "#898781",
    "in_progress": "#2a78d6",
    "blocked": "#d03b3b",
    "in_review": "#4a3aa7",
    "done": "#0ca30c",
    "cancelled": "#c3c2b7",
}


def graph_issues(issues: list[Issue]) -> list[Issue]:
    """Tasks, plus any issue on either end of a dependency edge."""
    keys = {i.id for i in issues if i.issue_type == "task"}
    for i in issues:
        if i.depends_on:
            keys.add(i.id)
            keys.update(i.depends_on)
    return [i for i in issues if i.id in keys]


def topo_order(issues: list[Issue]) -> list[str]:
    """Kahn's algorithm over ``depends_on`` edges: blockers before dependents.

    Ready issues are consumed in ``(priority, id)`` order — the repository's
    canonical ordering — so the result is deterministic. Raises ``ValueError``
    on a cycle (``JiraApi`` rejects cycle-forming links, so this only fires on
    corrupt data).
    """
    by_key = {i.id: i for i in issues}
    indegree = {i.id: 0 for i in issues}
    dependents: dict[str, list[str]] = {i.id: [] for i in issues}
    for issue in issues:
        for dep in issue.depends_on:
            if dep in by_key:
                indegree[issue.id] += 1
                dependents[dep].append(issue.id)
    ready = [(by_key[k].priority, k) for k, deg in indegree.items() if deg == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        _, key = heapq.heappop(ready)
        order.append(key)
        for nxt in dependents[key]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(ready, (by_key[nxt].priority, nxt))
    if len(order) != len(issues):
        raise ValueError("dependency cycle among issues")
    return order


def work_spans(store: Store) -> dict[str, tuple[int, int]]:
    """{issue key: worked ``(start, end)`` ticks} from the ``jira_ticket`` events.

    ``LIKE 'jira_ticket%'`` tolerates legacy dotted type strings the same way
    the calendars renderer does. Multiple events for one key (re-dispatch) are
    unioned to (min start, max end); instantaneous and cancelled rows reserve
    no time and are skipped.
    """
    rows = store.db.query_all(
        "SELECT start_tick, duration, payload_json FROM event "
        "WHERE type LIKE 'jira_ticket%' AND duration > 0 AND status != 'cancelled' "
        "ORDER BY start_tick, seq"
    )
    spans: dict[str, tuple[int, int]] = {}
    for r in rows:
        key = json.loads(r["payload_json"]).get("issue_key")
        if not key:
            continue
        start, end = r["start_tick"], r["start_tick"] + r["duration"]
        if key in spans:
            start, end = min(start, spans[key][0]), max(end, spans[key][1])
        spans[key] = (start, end)
    for key, start, end in _activity_work_rows(store):
        if key in spans:
            start, end = min(start, spans[key][0]), max(end, spans[key][1])
        spans[key] = (start, end)
    return spans


def _activity_work_rows(store: Store) -> list[tuple[str, int, int]]:
    """``(issue key, start, end)`` from ``jira_work`` activity rows.

    Completion-driven runs carry NPC work in the ``activity`` table; the span
    runs from dispatch to completion (interruptions included). Legacy run
    databases may lack the table or its ``done_tick`` column — treat as empty.
    """
    try:
        rows = store.db.query_all(
            "SELECT created_tick, done_tick, duration_needed, params_json FROM activity "
            "WHERE kind = 'jira_work' AND state != 'cancelled' ORDER BY id"
        )
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        key = json.loads(r["params_json"]).get("issue_key")
        if not key:
            continue
        end = r["done_tick"] if r["done_tick"] is not None else (
            r["created_tick"] + r["duration_needed"])
        out.append((key, r["created_tick"], end))
    return out


def _px(ticks: float) -> str:
    """Tick count scaled to pixels, rendered without a trailing ``.0``."""
    return f"{ticks * PX_PER_TICK:g}"


def _lanes(issues: list[Issue]) -> list[str]:
    """One lane per assignee (sorted), with ``unassigned`` last if needed."""
    people = sorted({i.assignee_id for i in issues if i.assignee_id})
    if any(i.assignee_id is None for i in issues):
        people.append("unassigned")
    return people


def _tooltip(issue: Issue, span: tuple[int, int]) -> str:
    return (
        f"{issue.id} — {issue.title} — @{issue.assignee_id or 'unassigned'}"
        f" — est {format_hours(issue.estimate_minutes)} — {issue.status}"
        f" — worked {format_tick(span[0])}–{format_tick(span[1])}"
    )


def _bar_geometry(
    issue: Issue, span: tuple[int, int], lane_index: dict[str, int]
) -> tuple[float, float, float]:
    """``(x, y, width)`` of an issue's bar (unscaled y; x/width in px floats)."""
    x = PAD + GUTTER_W + span[0] * PX_PER_TICK
    width = (span[1] - span[0]) * PX_PER_TICK
    lane = lane_index[issue.assignee_id or "unassigned"]
    y = PAD + HEADER_H + lane * (LANE_H + LANE_GAP) + BAR_INSET
    return x, y, width


def _svg_bar(issue: Issue, span: tuple[int, int], lane_index: dict[str, int]) -> str:
    """One ticket bar at its worked interval, key label inline when it fits."""
    x, y, width = _bar_geometry(issue, span, lane_index)
    color = STATUS_COLOR.get(issue.status, "#898781")
    height = LANE_H - 2 * BAR_INSET
    label = (
        f'<text x="{x + 4:g}" y="{y + height - 8:g}" font-size="10"'
        f' fill="#ffffff">{html.escape(issue.id)}</text>\n'
        if width >= MIN_LABEL_W
        else ""
    )
    return (
        f'<g><title>{html.escape(_tooltip(issue, span))}</title>\n'
        f'<rect x="{x:g}" y="{y:g}" width="{width:g}" height="{height}" rx="3"'
        f' fill="{color}"/>\n{label}</g>'
    )


def _svg_edges(
    issues: list[Issue], spans: dict[str, tuple[int, int]], lane_index: dict[str, int]
) -> list[str]:
    """One cubic curve per dependency: blocker bar's end → dependent bar's start."""
    by_key = {i.id: i for i in issues}
    mid = (LANE_H - 2 * BAR_INSET) / 2
    out = []
    for issue in issues:
        if issue.id not in spans:
            continue
        x2, y2, _ = _bar_geometry(issue, spans[issue.id], lane_index)
        for dep in issue.depends_on:
            if dep not in spans or dep not in by_key:
                continue
            x1, y1, w1 = _bar_geometry(by_key[dep], spans[dep], lane_index)
            sx, sy = x1 + w1, y1 + mid
            ex, ey = x2 - 4, y2 + mid  # stop short of the bar for the arrowhead
            out.append(
                f'<path d="M {sx:g},{sy:g} C {sx + 20:g},{sy:g} {ex - 20:g},{ey:g}'
                f' {ex:g},{ey:g}" fill="none" stroke="#52514e" stroke-width="1.2"'
                f' marker-end="url(#arrow)" opacity="0.7"/>'
            )
    return out


def render_timeline_svg(issues: list[Issue], spans: dict[str, tuple[int, int]]) -> str:
    """The week timeline: day grid + per-person lanes + ticket bars + dep arrows."""
    lanes = _lanes(issues)
    lane_index = {name: i for i, name in enumerate(lanes)}
    width = 2 * PAD + GUTTER_W + TICKS_PER_WEEK * PX_PER_TICK
    lanes_h = len(lanes) * (LANE_H + LANE_GAP) - LANE_GAP if lanes else 0
    height = 2 * PAD + HEADER_H + lanes_h
    grid_top, grid_bottom = PAD + HEADER_H, PAD + HEADER_H + lanes_h

    parts = [
        f'<svg width="{width:g}" height="{height}" xmlns="http://www.w3.org/2000/svg"'
        f' font-family="system-ui, sans-serif">',
        '<defs><marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4"'
        ' markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M 0 0 L 8 4 L 0 8 z" fill="#52514e"/></marker></defs>',
    ]
    # Day columns: boundary line at each day start/end, weekday name centered.
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
    # Lane labels.
    for name, i in lane_index.items():
        y = PAD + HEADER_H + i * (LANE_H + LANE_GAP) + LANE_H // 2 + 4
        parts.append(
            f'<text x="{PAD}" y="{y}" font-size="12" font-weight="600"'
            f' fill="#0b0b0b">{html.escape(name)}</text>'
        )
    parts += _svg_edges(issues, spans, lane_index)
    parts += [
        _svg_bar(i, spans[i.id], lane_index) for i in issues if i.id in spans
    ]
    parts.append("</svg>")
    return "\n".join(parts)


def _order_item(issue: Issue) -> str:
    """One list entry, in the jira_board example's annotation style."""
    return (
        f"<li><strong>{html.escape(issue.id)}</strong> — {html.escape(issue.title)}"
        f" — @{html.escape(issue.assignee_id or 'unassigned')}"
        f" — est {format_hours(issue.estimate_minutes)}"
        f" — [{html.escape(issue.status)}]</li>"
    )


def render_jira_html(
    issues: list[Issue],
    order: list[str],
    spans: dict[str, tuple[int, int]],
    heading: str,
) -> str:
    by_key = {i.id: i for i in issues}
    legend = "".join(
        f'<span class="key"><span class="swatch" style="background:{color}"></span>'
        f"{status}</span>"
        for status, color in STATUS_COLOR.items()
    )
    items = "\n".join(_order_item(by_key[k]) for k in order)
    unscheduled = [i for i in issues if i.id not in spans]
    unscheduled_html = ""
    if unscheduled:
        rows = "\n".join(_order_item(i) for i in unscheduled)
        unscheduled_html = (
            "<h2>Not scheduled</h2>\n"
            '<p class="hint">Tickets with no worked interval in this run.</p>\n'
            f"<ul>\n{rows}\n</ul>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Jira tasks — {html.escape(heading)}</title>
<style>
  body {{ font: 13px system-ui, sans-serif; margin: 24px; background: #f9f9f7; color: #0b0b0b; }}
  h1 {{ font-size: 18px; }} h2 {{ font-size: 15px; margin-top: 28px; }}
  .legend {{ margin: 8px 0 16px; color: #52514e; }}
  .key {{ margin-right: 14px; }}
  .swatch {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px;
             margin-right: 5px; vertical-align: baseline; }}
  .graph {{ overflow-x: auto; background: #fcfcfb; border: 1px solid #e1e0d9;
            border-radius: 6px; padding: 8px; }}
  ol, ul {{ line-height: 1.7; }}
  .hint {{ color: #898781; }}
</style></head><body>
<h1>Jira tickets on the week timeline — {html.escape(heading)}</h1>
<p class="hint">One lane per person over the Mon–Fri 09:00–17:00 work week;
each bar spans the interval the ticket was actually worked. Arrows point from
a blocking ticket to the ticket it blocks.</p>
<div class="legend">{legend}</div>
<div class="graph">
{render_timeline_svg(issues, spans)}
</div>
<h2>Completion order</h2>
<p class="hint">A valid order to finish everything — every task appears after
all tasks it depends on.</p>
<ol>
{items}
</ol>
{unscheduled_html}
</body></html>
"""


def write_jira_tasks(run_id: str, root: Path = RUNS_DIR) -> Path:
    """Render ``run_id``'s ticket timeline and write ``runs/<id>/jira_tasks.html``."""
    db = Env.world_path(run_id, root)
    if not db.exists():
        raise ConfigurationError(
            f"no run database at {db}", details={"run_id": run_id, "path": str(db)}
        )
    store = Store.open(str(db))
    try:
        repo = JiraRepository(store)
        repo.ensure_schema()
        issues = graph_issues(repo.list_issues())
        spans = work_spans(store)
    finally:
        store.close()
    order = topo_order(issues)
    page = render_jira_html(issues, order, spans, heading=f"run {run_id}")

    out = db.parent / "jira_tasks.html"
    out.write_text(page, encoding="utf-8")
    return out
