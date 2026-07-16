"""The "Team, No Jira" scenario — a week of real work that never touches the board.

Monday's kickoff establishes the Meeting Transcripts v1 project
(``pm/transcript/project.md``): six tasks, each with a DRI — and the team agrees
to "file the tickets later". Nobody does. The daily meeting payloads create and
update the informal ``task`` rows as each meeting ends, and the authored
transcripts (``pm/transcript/no-jira-*.md``) narrate the same statuses — so by
Friday four tasks are done and two are carrying over, while the Jira board has
never held a single ticket. The trap for a PM: ``read_jira_board`` says nothing
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

# Status updates each day's meeting reports (and its transcript narrates).
# Monday's kickoff payload carries the full project_tasks() breakdown instead.
DAY_UPDATES: dict[int, list[dict[str, str]]] = {
    1: [{"id": "NOTES-1", "status": "in_progress"},
        {"id": "NOTES-2", "status": "in_progress"},
        {"id": "NOTES-6", "status": "in_progress"}],
    2: [{"id": "NOTES-2", "status": "done"},
        {"id": "NOTES-3", "status": "in_progress"},
        {"id": "NOTES-4", "status": "in_progress"}],
    3: [{"id": "NOTES-1", "status": "done"},
        {"id": "NOTES-6", "status": "done"},
        {"id": "NOTES-5", "status": "in_progress"}],
    4: [{"id": "NOTES-3", "status": "done"}],
}


def _schedule_meetings(env: Env) -> None:
    """Monday kickoff (project details + full breakdown) + Tue-Fri standups."""
    for day in range(STANDUP_DAYS):
        kickoff = day == 0
        body = standup_transcript(day, prefix="no-jira")
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
    print(f"Built scenario {SCENARIO!r} at runs/{SCENARIO}/ (six tasks with DRIs "
          "tracked only in meeting notes; the Jira board stays empty all week).")
