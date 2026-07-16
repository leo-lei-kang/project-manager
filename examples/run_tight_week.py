"""Runnable demo of the "Tight Week" scenario — a week the team barely finishes.

    uv run python examples/run_tight_week.py

Builds ``pm.scenarios.tight_week`` into ``runs/tight_week/`` — a capacity-saturated
``GA`` board (1 epic, 5 stories, 66 pre-assigned tasks) whose estimates tile each
member's free calendar segments between the eleven pre-booked meetings exactly.
Worked in dependency + priority order (the default persona), the last task —
alice's GA sign-off — completes at exactly Friday 17:00, tick 2400 of 2400.

Then it runs the bounded Mon 09:00 → Fri 17:00 loop with each engineer working
their own queue (``assignee_pickup_hook``), printing per day the tasks completed
and the board's status rollup.

Unlike the other examples this one **persists its run**: after it exits,
``runs/tight_week/seed.db`` holds the immutable seeded starting state and
``runs/tight_week/world.db`` holds the completed week, both inspectable with
``uv run sqlite3`` or gradeable with ``uv run pm eval --run-id tight_week``.
"""

from __future__ import annotations

from collections import Counter

from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.behavior import assignee_pickup_hook
from pm.scenarios import tight_week
from pm.sim.clock import MINUTES_PER_WORKDAY, WEEKDAYS, WORKDAYS, format_tick
from pm.sim.simulation import Simulation


def report_day(day: int, store, api: JiraApi) -> None:
    """Print the tasks completed during ``day`` and the board's status rollup."""
    lo, hi = day * MINUTES_PER_WORKDAY, (day + 1) * MINUTES_PER_WORKDAY
    print(f"===== {WEEKDAYS[day]} closed =====")
    print("  completed:")
    for e in store.read_log():
        if e.kind == "jira_ticket.done" and lo <= e.sim_tick < hi:
            print(f"    {format_tick(e.sim_tick):>10}  {e.actor:<8} {e.payload.get('issue_key', '')}")
    statuses = Counter(
        i.status for i in api.search(project_id=tight_week.PROJECT_ID, issue_type="task"))
    rollup = ", ".join(f"{s}={n}" for s, n in sorted(statuses.items()))
    print(f"  board: {rollup}")


def main() -> None:
    env = tight_week.build()  # persists to runs/tight_week/{seed.db, world.db}
    api = JiraApi(JiraRepository(env.store), env.engine)
    sim = Simulation(env)

    pickup = assignee_pickup_hook(api, tight_week.MEMBERS, tight_week.PROJECT_ID)

    print(f"Simulating the tight week, starting {sim.now_label()} "
          f"({env.scheduler.pending_count()} events queued)\n")

    def on_tick(s: Simulation) -> None:
        pickup(s)  # engineers pick up their next ready issue, in priority order
        now = s.clock.now()
        if now % MINUTES_PER_WORKDAY == 0 and now > 0:  # a full day just closed
            report_day(now // MINUTES_PER_WORKDAY - 1, s.store, api)

    summary = sim.run(on_tick=on_tick)
    report_day(WORKDAYS - 1, env.store, api)  # Friday closes after the loop exits

    last_done = env.store.db.query_one(
        "SELECT MAX(done_tick) AS t FROM event WHERE type = 'jira_ticket'")["t"]
    print(f"\nReached {sim.now_label()} (tick {summary.final_tick}); "
          f"{summary.events_fired} event transitions fired.")
    print(f"Last task completed at tick {last_done} of 2400 — the week is used to the minute.")
    world = env.world_path(env.run_id, env.root)
    seed = env.seed_path(env.run_id, env.root)
    print(f"Run persisted: {world} (world) and {seed} (seed).")
    env.close()


if __name__ == "__main__":
    main()
