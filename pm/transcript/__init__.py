"""Authored meeting transcripts — the meeting record the PM reviews.

One markdown file per team_no_jira meeting (``no-jira-0.md`` … ``no-jira-4.md``:
Monday's kickoff, then Tue–Fri standups). The scenario wires each file in as the
meeting's ``transcript_body``; when the meeting ends,
:class:`~pm.sim.events.MeetingEvent` persists it to the ``transcript`` table
with ``available_tick`` = the meeting's end, and the agent reads it via
``read_transcripts``.

The week they narrate: the Meeting Transcripts v1 project is tracked in these
notes only — DRIs and statuses never reach the Jira board.
"""

from __future__ import annotations

from pathlib import Path

STANDUP_DAYS = 5


def _read(name: str) -> str:
    return Path(__file__).with_name(name).read_text(encoding="utf-8")


def standup_transcript(day: int) -> str:
    """The authored markdown transcript for weekday ``day`` (0=Mon .. 4=Fri)."""
    if not 0 <= day < STANDUP_DAYS:
        raise ValueError(f"no standup transcript for day {day}; expected 0..{STANDUP_DAYS - 1}")
    return _read(f"no-jira-{day}.md")


def project_brief(board: str = "team") -> str:
    """The project doc for ``board`` — the project details and its task breakdown.

    One "Meeting Transcripts v1" project, three board-sized breakdowns:
    ``project_team.md`` (five DRIs), ``project_two_engineers.md`` (alice+clare),
    ``project_single_engineer.md`` (alice alone).
    """
    return _read(f"project_{board}.md")


def project_tasks(board: str = "team") -> list[dict[str, str]]:
    """The board's task breakdown, parsed from its project doc's table.

    The doc is the single source: scenarios seed the informal ``task`` rows
    (and, selectively, Jira tickets) from these entries, and the team kickoff
    transcript embeds the same document — nothing can drift.
    """
    tasks = []
    for line in project_brief(board).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 5 and cells[0].startswith("NOTES-"):
            tasks.append({"id": cells[0], "title": cells[1], "dri_id": cells[2],
                          "status": cells[3], "estimate_minutes": cells[4]})
    return tasks
