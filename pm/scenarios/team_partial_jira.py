"""The "Team, Partial Jira" scenario — a sliver of the project on the board.

Same Meeting Transcripts v1 project (``pm/transcript/project_team.md``) as
:mod:`~pm.scenarios.team_no_jira`, but three of the 25 tasks were actually
filed as Jira tickets (NOTES-1, NOTES-2, NOTES-6 — assignee = DRI) and the team
works them on the board as usual. The other 22 exist only in the meeting
notes: the Monday kickoff establishes all 25 in the informal ``task`` table,
and the week's meeting payloads keep their statuses current. The trap for a PM:
the board looks healthy and complete, but ``read_transcripts`` and the ``task``
table show a project eight times the size.
"""

from __future__ import annotations

from pathlib import Path

from pm.env.environment import RUNS_DIR, Env
from pm.npc.cast import MEMBERS as _CAST_MEMBERS
from pm.npc.cast import seed_cast
from pm.scenarios.project_board import PROJECT_ID as PROJECT_ID
from pm.scenarios.project_board import seed_project_board
from pm.scenarios.team_no_jira import DAY_UPDATES
from pm.sim.clock import MINUTES_PER_WORKDAY
from pm.sim.events import MeetingEvent
from pm.transcript import STANDUP_DAYS, project_brief, project_tasks

SCENARIO = "team_partial_jira"
JIRA_IDS = ("NOTES-1", "NOTES-2", "NOTES-6")  # the half that got filed

CAST = list(_CAST_MEMBERS)
MEMBERS = [c.id for c in CAST]


def _schedule_meetings(env: Env) -> None:
    """Monday kickoff (project doc as the transcript) + Tue-Fri standups.

    Only the kickoff carries an authored body; the standups rely on the
    always-on transcript default (empty body) while their payloads keep the
    informal task statuses current.
    """
    for day in range(STANDUP_DAYS):
        kickoff = day == 0
        payload = {
            "meeting_id": f"partial-{day}",
            "kind": "kickoff" if kickoff else "standup",
            "title": "Project kickoff" if kickoff else "Daily standup",
            "attendees": MEMBERS,
            "tasks": project_tasks() if kickoff else DAY_UPDATES[day],
        }
        if kickoff:
            payload["transcript_body"] = "# Project kickoff — Monday 11:00\n\n" + project_brief()
        env.engine.schedule(MeetingEvent(
            owner_id="alice", start_tick=day * MINUTES_PER_WORKDAY + 120,
            duration=60 if kickoff else 30, payload=payload))


def build(run_id: str = SCENARIO, *, seed: int = 42, root: Path = RUNS_DIR,
          force: bool = True) -> Env:
    """Create the run: seed the team, the half-filed board, and the meeting week."""
    env = Env.make(SCENARIO, run_id, seed, force=force, root=root)
    seed_cast(env.store, cast=CAST)
    seed_project_board(env, jira_ids=JIRA_IDS)
    _schedule_meetings(env)
    env.store.db.backup_to(Env.seed_path(run_id, root))
    return env


if __name__ == "__main__":
    build()
    print(f"Built scenario {SCENARIO!r} at runs/{SCENARIO}/ (three of 25 tasks on "
          "the Jira board; the rest live only in the meeting notes).")
