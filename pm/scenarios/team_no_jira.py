"""The "Team, No Jira" scenario — a week of real work that never touches the board.

Monday's kickoff establishes the Meeting Transcripts v1 project
(``pm/transcript/project_team.md``): 25 tasks — a zero-slack week for each of the
five DRIs — and the team agrees to "file the tickets later". Nobody does. The
daily meeting payloads create and update the informal ``task`` rows as each
meeting ends, and the authored transcripts (``pm/transcript/no-jira-*.md``)
narrate the same statuses — so by Friday 15 tasks are done, five are mid-flight,
and five haven't started, while the Jira board has never held a single ticket. The trap for a PM: ``read_jira_board`` says nothing
is happening; ``read_transcripts`` (and the ``task`` table) tell the truth.

There is no board work to dispatch, so the pickup hook idles all week; the
meetings are the only events.
"""

from __future__ import annotations

from pathlib import Path

from pm.env.environment import RUNS_DIR, Env
from pm.npc.cast import MEMBERS as _CAST_MEMBERS
from pm.npc.cast import seed_cast
from pm.scenarios.project_board import PROJECT_ID as PROJECT_ID
from pm.scenarios.project_board import seed_project_board
from pm.sim.clock import MINUTES_PER_WORKDAY
from pm.sim.events import MeetingEvent
from pm.transcript import STANDUP_DAYS, project_brief, project_tasks, standup_transcript

SCENARIO = "team_no_jira"

CAST = list(_CAST_MEMBERS)
MEMBERS = [c.id for c in CAST]

# Each member works their queue in order; every standup reports the previous
# task done and the next one started. By Friday: 15 done, 5 in progress,
# 5 (each member's last) untouched.
# bob 1→7→8→9→10, david 2→5→11→12→13, alice 3→14→15→16→17,
# clare 4→18→19→20→21, elieen 6→22→23→24→25.
# Monday's kickoff payload carries the full project_tasks() breakdown instead.
_QUEUE_BY_DAY = [
    ("NOTES-1", "NOTES-2", "NOTES-3", "NOTES-4", "NOTES-6"),
    ("NOTES-7", "NOTES-5", "NOTES-14", "NOTES-18", "NOTES-22"),
    ("NOTES-8", "NOTES-11", "NOTES-15", "NOTES-19", "NOTES-23"),
    ("NOTES-9", "NOTES-12", "NOTES-16", "NOTES-20", "NOTES-24"),
]
DAY_UPDATES: dict[int, list[dict[str, str]]] = {}
for _day in range(1, STANDUP_DAYS):
    _finished = _QUEUE_BY_DAY[_day - 2] if _day > 1 else ()
    DAY_UPDATES[_day] = (
        [{"id": t, "status": "done"} for t in _finished]
        + [{"id": t, "status": "in_progress"} for t in _QUEUE_BY_DAY[_day - 1]])


def _schedule_meetings(env: Env) -> None:
    """Monday kickoff (project details + full breakdown) + Tue-Fri standups."""
    for day in range(STANDUP_DAYS):
        kickoff = day == 0
        body = standup_transcript(day)
        if kickoff:
            body += "\n" + project_brief()  # the kickoff embeds the project doc
        payload = {
            "meeting_id": f"no-jira-{day}",
            "kind": "kickoff" if kickoff else "standup",
            "title": "Project kickoff" if kickoff else "Daily standup",
            "attendees": MEMBERS,
            "transcript_id": f"tr-no-jira-{day}",
            "transcript_body": body,
            "tasks": project_tasks() if kickoff else DAY_UPDATES[day],
        }
        env.engine.schedule(MeetingEvent(
            owner_id="alice", start_tick=day * MINUTES_PER_WORKDAY + 120,
            duration=60 if kickoff else 30, payload=payload))


def build(run_id: str = SCENARIO, *, seed: int = 42, root: Path = RUNS_DIR,
          force: bool = True) -> Env:
    """Create the run: seed the team, the empty board, and the meeting week."""
    env = Env.make(SCENARIO, run_id, seed, force=force, root=root)
    seed_cast(env.store, cast=CAST)
    seed_project_board(env, jira_ids=())
    _schedule_meetings(env)
    env.store.db.backup_to(Env.seed_path(run_id, root))
    return env


if __name__ == "__main__":
    build()
    print(f"Built scenario {SCENARIO!r} at runs/{SCENARIO}/ (25 tasks with DRIs "
          "tracked only in meeting notes; the Jira board stays empty all week).")
