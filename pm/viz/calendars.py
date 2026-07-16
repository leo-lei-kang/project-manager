"""Weekly calendar per person from a run's ``world.db``, rendered to static HTML.

A person's calendar is derived from the durative ``event`` rows (resolved
start/duration; a meeting covers its owner plus attendees), so it works for
seed-state, mid-run, and completed runs alike. :func:`write_calendars` reads
``runs/<run_id>/world.db`` and writes ``runs/<run_id>/calendars.html``: one
Mon–Fri 09:00–17:00 grid per person, blocks colored by kind (meeting / work /
OOO), positioned at 1px per tick (minute). Zero dependencies beyond the project
itself.
"""

from __future__ import annotations

import html
import json
import sqlite3
from pathlib import Path
from typing import NamedTuple

from pm.db.store import Store
from pm.env.environment import RUNS_DIR, Env
from pm.exceptions import ConfigurationError
from pm.sim.clock import MINUTES_PER_WORKDAY, WEEKDAYS, format_tick
from pm.world.models import Person


class Block(NamedTuple):
    kind: str
    start: int  # tick, half-open [start, end)
    end: int
    title: str


def _issue_titles(store: Store) -> dict[str, str]:
    """{issue key: title}; empty when the additive ``issue`` table is absent."""
    present = store.db.query_one(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'issue'"
    )
    if not present:
        return {}
    return {
        r["id"]: r["title"]
        for r in store.db.query_all("SELECT id, title FROM issue")
    }


def load_blocks(store: Store) -> dict[str, list[Block]]:
    """Calendar blocks per person, in start order, derived from ``event`` rows.

    Not from ``occupancy``: that table only holds live reservations — the
    engine frees each block when its event completes — so a finished run's
    calendar survives only on the event rows (resolved start/duration). A
    meeting occupies its owner plus payload attendees (the attendance rule of
    ``pm.sim.calendar.people_for``); other durative events occupy just the
    owner. Raw SQL rather than ``Event.from_row``: older run databases store
    legacy event types like ``meeting.start`` that the current ``EventType``
    enum rejects — the type's first dotted segment is the stable part.
    """
    titles = _issue_titles(store)
    rows = store.db.query_all(
        "SELECT type, owner_id, start_tick, duration, payload_json FROM event "
        "WHERE duration > 0 AND status != 'cancelled' ORDER BY start_tick, seq"
    )
    blocks: dict[str, list[Block]] = {}
    for r in rows:
        kind = r["type"].split(".", 1)[0]
        payload = json.loads(r["payload_json"])
        issue_key = payload.get("issue_key", "")
        title = payload.get("title") or titles.get(issue_key) or issue_key or kind
        people = {r["owner_id"]}
        if kind == "meeting":
            people.update(payload.get("attendees", []))
        people.discard("")
        block = Block(kind, r["start_tick"], r["start_tick"] + r["duration"], title)
        for pid in people:
            blocks.setdefault(pid, []).append(block)
    # Completion-driven runs carry NPC work in the activity table (meetings/OOO
    # still come from their event rows above — the bridge kinds are skipped to
    # avoid double-drawing). Legacy DBs may lack the table/column: treat as empty.
    try:
        rows = store.db.query_all(
            "SELECT attendees_json, created_tick, done_tick, duration_needed, params_json "
            "FROM activity WHERE kind = 'jira_work' AND state != 'cancelled' ORDER BY id"
        )
    except sqlite3.OperationalError:
        rows = []
    for r in rows:
        params = json.loads(r["params_json"])
        issue_key = params.get("issue_key", "")
        title = titles.get(issue_key) or issue_key or "jira_work"
        end = r["done_tick"] if r["done_tick"] is not None else (
            r["created_tick"] + r["duration_needed"])
        block = Block("jira_ticket", r["created_tick"], end, title)
        for pid in json.loads(r["attendees_json"]):
            blocks.setdefault(pid, []).append(block)
    return blocks


def day_segments(block: Block) -> list[tuple[int, int, int]]:
    """``(day, top_px, height_px)`` slices of a block, split at day boundaries.

    Tick space is contiguous work-minutes (17:00 rolls straight into the next
    09:00), so a resolved block can span days; each day column gets its slice.
    """
    out = []
    start = block.start
    while start < block.end:
        day = start // MINUTES_PER_WORKDAY
        day_end = (day + 1) * MINUTES_PER_WORKDAY
        seg_end = min(block.end, day_end)
        out.append((day, start % MINUTES_PER_WORKDAY, seg_end - start))
        start = seg_end
    return out


_KIND_CLASSES = ("meeting", "jira_ticket", "ooo")
_KIND_LABELS = {"meeting": "Meeting", "jira_ticket": "Work (Jira)", "ooo": "Out of office"}


def _block_divs(blocks: list[Block]) -> dict[int, list[str]]:
    """Rendered block divs grouped by weekday column."""
    per_day: dict[int, list[str]] = {}
    for b in blocks:
        css = b.kind if b.kind in _KIND_CLASSES else "other"
        tooltip = f"{b.title} — {format_tick(b.start)}–{format_tick(b.end)}"
        for day, top, height in day_segments(b):
            if day >= len(WEEKDAYS):
                continue
            per_day.setdefault(day, []).append(
                f'<div class="block {css}" style="top:{top}px;height:{height}px"'
                f' title="{html.escape(tooltip)}">{html.escape(b.title)}</div>'
            )
    return per_day


def _person_section(person: Person, blocks: list[Block]) -> str:
    per_day = _block_divs(blocks)
    # Hour label at each grid line; +20px skips the day header, -7px centers
    # the 10px label on the line.
    gutter = "".join(
        f'<span class="hour" style="top:{13 + h * 60}px">{9 + h:02d}:00</span>'
        for h in range(9)
    )
    days = "".join(
        f'<div class="day"><h3>{name}</h3>'
        f'<div class="slots">{"".join(per_day.get(day, []))}</div></div>'
        for day, name in enumerate(WEEKDAYS)
    )
    role = f" — {html.escape(person.role)}" if person.role else ""
    return (
        f"<section><h2>{html.escape(person.name)}{role}</h2>\n"
        f'<div class="week"><div class="gutter">{gutter}</div>{days}</div></section>'
    )


def render_calendars_html(
    people: list[Person], blocks: dict[str, list[Block]], heading: str
) -> str:
    legend = "".join(
        f'<span class="key"><span class="swatch {kind}"></span>{label}</span>'
        for kind, label in _KIND_LABELS.items()
    )
    sections = "\n".join(_person_section(p, blocks.get(p.id, [])) for p in people)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Calendars — {html.escape(heading)}</title>
<style>
  body {{ font: 13px system-ui, sans-serif; margin: 24px; background: #f9f9f7; color: #0b0b0b; }}
  h1 {{ font-size: 18px; }} h2 {{ font-size: 15px; margin: 28px 0 8px; }}
  h3 {{ font-size: 12px; font-weight: 600; color: #52514e; height: 16px; margin: 0 0 4px; }}
  .legend {{ margin: 8px 0 4px; color: #52514e; }}
  .key {{ margin-right: 14px; }}
  .swatch {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px;
             margin-right: 5px; }}
  .week {{ display: flex; gap: 4px; max-width: 1100px; }}
  .gutter {{ position: relative; width: 44px; }}
  .hour {{ position: absolute; right: 4px; font-size: 10px; color: #898781; }}
  .day {{ flex: 1; min-width: 120px; }}
  .slots {{ position: relative; height: 480px; border: 1px solid #e1e0d9;
            background: #fcfcfb; border-radius: 3px;
            background-image: repeating-linear-gradient(
              to bottom, transparent 0 59px, #e1e0d9 59px 60px); }}
  .block {{ position: absolute; left: 2px; right: 2px; box-sizing: border-box;
            padding: 1px 4px; border-radius: 3px; overflow: hidden;
            font-size: 11px; color: #ffffff; }}
  .meeting {{ background: #256abf; }}
  .jira_ticket {{ background: #008300; }}
  .ooo {{ background: #52514e; }}
  .other {{ background: #898781; }}
</style></head><body>
<h1>Calendars — {html.escape(heading)}</h1>
<div class="legend">{legend}</div>
{sections}
</body></html>
"""


def write_calendars(run_id: str, root: Path = RUNS_DIR) -> Path:
    """Render ``run_id``'s per-person calendars and write ``runs/<id>/calendars.html``."""
    db = Env.world_path(run_id, root)
    if not db.exists():
        raise ConfigurationError(
            f"no run database at {db}", details={"run_id": run_id, "path": str(db)}
        )
    store = Store.open(str(db))
    try:
        people = store.list_people()
        blocks = load_blocks(store)
        heading = f"run {run_id} @ {format_tick(store.get_tick())}"
    finally:
        store.close()
    page = render_calendars_html(people, blocks, heading)

    out = db.parent / "calendars.html"
    out.write_text(page, encoding="utf-8")
    return out
