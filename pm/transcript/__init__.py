"""Authored standup transcripts — the meeting record the PM reviews.

One markdown file per tight_week standup (``standup-0.md`` … ``standup-4.md``,
Mon–Fri). The scenario wires each file in as the standup's ``transcript_body``;
when the meeting ends, :class:`~pm.sim.events.MeetingEvent` persists it to the
``transcript`` table with ``available_tick`` = the meeting's end, and the agent
reads it via ``read_transcripts``.

The thread that runs through the week: a customer request for **live caption
translation** — deliberately *not* on the GA board — surfaces Monday. Bob wants
to build it; Alice wants the team to stay on the launch blockers. Nobody on the
team can settle it: prioritizing is the PM's call.
"""

from __future__ import annotations

from pathlib import Path

STANDUP_DAYS = 5


def _read(name: str) -> str:
    return Path(__file__).with_name(name).read_text(encoding="utf-8")


def standup_transcript(day: int, prefix: str = "standup") -> str:
    """The authored markdown transcript for weekday ``day`` (0=Mon .. 4=Fri).

    ``prefix`` selects the transcript set: ``standup`` (tight_week's week) or
    ``no-jira`` (the team_no_jira week).
    """
    if not 0 <= day < STANDUP_DAYS:
        raise ValueError(f"no standup transcript for day {day}; expected 0..{STANDUP_DAYS - 1}")
    return _read(f"{prefix}-{day}.md")


def project_brief() -> str:
    """The whole ``project.md`` — the project details and its task breakdown."""
    return _read("project.md")


def project_tasks() -> list[dict[str, str]]:
    """The project's task breakdown, parsed from ``project.md``'s table.

    ``project.md`` is the single source: scenarios seed the informal ``task``
    rows (and, selectively, Jira tickets) from these entries, and the Monday
    kickoff transcript embeds the same document — nothing can drift.
    """
    tasks = []
    for line in project_brief().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 5 and cells[0].startswith("NOTES-"):
            tasks.append({"id": cells[0], "title": cells[1], "dri_id": cells[2],
                          "status": cells[3], "estimate_minutes": cells[4]})
    return tasks
