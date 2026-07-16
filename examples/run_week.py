"""Runnable demo of a full simulated work week — Monday 09:00 to Friday 17:00.

    uv run python examples/run_week.py

Builds the "Team Week" scenario (``pm.scenarios.team_week``): the engineer cast
from ``pm.npc.cast`` (Alice/Bob/Clare/David/Elieen plus the CTO), an assigned,
dependency-linked Jira board (project ``TRANS`` — 1 epic, 3 stories, 8 tasks), and
eleven pre-scheduled meetings. Then it runs the bounded simulation loop from the
start of Monday to the end of Friday, one work-minute at a time, while each
engineer autonomously works their assigned issues (``pm.npc.behavior``'s
``assignee_pickup_hook``). Meetings fire on the clock and dependencies cascade —
a finished blocker unblocks its dependents.

As each day closes it prints that day's report: the events that happened (meetings
and board work) and the board's end-of-day issue states, so the week's progress is
visible day by day rather than only at Friday.

Self-contained: writes to a throwaway directory, so nothing lands in ./runs.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.behavior import assignee_pickup_hook
from pm.scenarios import team_week
from pm.sim.clock import MINUTES_PER_WORKDAY, WEEKDAYS, WORKDAYS, format_tick
from pm.sim.simulation import Simulation


def report_day(day: int, store, api: JiraApi) -> None:
    """Print the events during ``day`` and the board's end-of-day issue states.

    Reads the board at call time, so calling it right after ``day`` closes (the
    next morning boundary, or after run() for Friday) yields that day's end-of-day
    snapshot — issue status mutates as the engineers work the board.
    """
    lo, hi = day * MINUTES_PER_WORKDAY, (day + 1) * MINUTES_PER_WORKDAY
    print(f"===== {WEEKDAYS[day]} closed =====")
    print("  events:")
    for e in store.read_log():
        if e.kind in ("action", "meeting.kind"):  # jira bookkeeping / internal marker
            continue
        if lo <= e.sim_tick < hi:
            detail = e.payload.get("issue_key") or e.payload.get("title") or ""
            print(f"    {format_tick(e.sim_tick):>10}  {e.actor:<8} {e.kind:<20} {detail}")
    print("  end-of-day board:")
    for i in api.search(project_id=team_week.PROJECT_ID, issue_type="task"):
        print(f"    {i.id:<10} {i.title:<26} {i.status:<12} {i.assignee_id or '-'}")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = team_week.build(run_id="week", root=Path(tmp), force=True)
        repo = JiraRepository(env.store)
        repo.ensure_schema()
        api = JiraApi(repo, env.engine)
        sim = Simulation(env)

        pickup = assignee_pickup_hook(api, team_week.MEMBERS, team_week.PROJECT_ID)

        print(f"Simulating the work week, starting {sim.now_label()} "
              f"({env.scheduler.pending_count()} events queued)\n")

        def on_tick(s: Simulation) -> None:
            pickup(s)  # engineers pick up their next ready issue
            now = s.clock.now()
            if now % MINUTES_PER_WORKDAY == 0:  # each morning at 09:00
                if now > 0:  # a full day just closed
                    report_day(now // MINUTES_PER_WORKDAY - 1, s.store, api)
                print(f"----- {s.now_label()} -----")

        summary = sim.run(on_tick=on_tick)

        report_day(WORKDAYS - 1, env.store, api)  # Friday closes after the loop exits

        print(f"\nReached {sim.now_label()} (tick {summary.final_tick}); "
              f"{summary.events_fired} event transitions fired over the week.")
        env.close()


if __name__ == "__main__":
    main()
