"""NPC coworkers' week on the per-NPC calendar — meetings vs. work, resolved.

    uv run python examples/npc_calendar.py

Builds the "Team with Jira" scenario (``pm.scenarios.team_with_jira``): a six-person team,
a capacity-saturated 66-task Jira board, and eleven pre-scheduled meetings (daily
standups, Alice's 1:1s, a Friday team meeting, a Wednesday ad-hoc). Then it runs the work week while
each coworker autonomously works their assigned issues, so their work
(``JiraTicketEvent``, low priority) collides with the meetings (high priority) on the
shared per-NPC calendar (``pm.sim.calendar``).

The calendar resolves every conflict at schedule time: a meeting *bumps* work —
planned work *yields* (it starts after the meeting) and in-progress work *pauses &
resumes* (its duration is extended by the meeting's length). Both resolutions are
persisted onto the event row (a shifted ``start_tick`` / an extended ``duration``),
so after the week the ``event`` table *is* each coworker's resolved calendar. This
prints that per-coworker timeline plus a summary of what the calendar deferred or
paused.

Because the scenario's eleven meetings are all booked up front, work dispatched
later simply *yields* around them. To also show the *pause & resume* path — a
meeting landing on work that is already underway — the run injects one surprise
mid-week meeting onto a coworker while their task is active.

Self-contained: writes to a throwaway directory.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pm.jira.api import JiraApi  # noqa: E402
from pm.jira.repository import JiraRepository  # noqa: E402
from pm.npc.behavior import assignee_pickup_hook  # noqa: E402
from pm.scenarios import team_with_jira  # noqa: E402
from pm.sim.clock import format_tick  # noqa: E402
from pm.sim.events import MeetingEvent  # noqa: E402
from pm.sim.simulation import Simulation  # noqa: E402


def _week_events(store) -> list[dict]:
    """All meeting + work events, in resolved start order, with parsed payloads."""
    rows = store.db.query_all(
        "SELECT * FROM event WHERE type IN ('meeting', 'jira_ticket') "
        "ORDER BY start_tick, seq"
    )
    out = []
    for r in rows:
        ev = dict(r)
        ev["payload"] = json.loads(r["payload_json"])
        out.append(ev)
    return out


def _attended_by(ev: dict, pid: str) -> bool:
    """Does ``pid`` occupy this event's window (a work owner, or a meeting attendee)?"""
    if ev["type"] == "meeting":
        return pid == ev["owner_id"] or pid in ev["payload"].get("attendees", [])
    return ev["owner_id"] == pid


def _title(api: JiraApi, ev: dict) -> str:
    """The meeting's title, or the worked issue's title."""
    if ev["type"] == "meeting":
        return ev["payload"].get("title", "meeting")
    issue = api.get_issue(ev["payload"].get("issue_key", ""))
    return issue.title if issue else ev["payload"].get("issue_key", "work")


def _paused_minutes(api: JiraApi, ev: dict) -> int:
    """Minutes a work event was extended by (a meeting cut into active work)."""
    if ev["type"] != "jira_ticket":
        return 0
    issue = api.get_issue(ev["payload"].get("issue_key", ""))
    if issue is None:
        return 0
    return max(0, ev["duration"] - issue.estimate_minutes)


def _run_hook(api: JiraApi):
    """The per-tick driver: coworkers work their board, plus one surprise meeting.

    Wraps :func:`assignee_pickup_hook`; at Mon 12:00 it drops a 30-min ad-hoc onto
    Elieen while her design task is in progress, so the calendar must *pause & resume*
    it (rather than defer, as it does for meetings booked before the work exists).
    """
    pickup = assignee_pickup_hook(api, team_with_jira.MEMBERS, team_with_jira.PROJECT_ID)
    surprise_tick = team_with_jira.at(0, 12)
    injected = False

    def hook(sim: Simulation) -> None:
        nonlocal injected
        pickup(sim)
        if not injected and sim.clock.now() == surprise_tick:
            sim.schedule(MeetingEvent(
                owner_id="xavier", initiator_id="xavier",
                start_tick=surprise_tick, duration=30,
                payload={"meeting_id": "surprise", "kind": "adhoc",
                         "title": "Surprise incident review", "attendees": ["elieen"]},
            ))
            injected = True

    return hook


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = team_with_jira.build(run_id="npc-calendar", root=Path(tmp), force=True)
        store = env.store
        repo = JiraRepository(store)
        repo.ensure_schema()
        api = JiraApi(repo, env.engine)
        names = {c.id: c.name for c in team_with_jira.CAST}

        print(f"=== {team_with_jira.SCENARIO}: the team ===")
        for c in team_with_jira.CAST:
            tag = "" if c.works else "  (doesn't implement)"
            print(f"  {c.id:<8} {c.name:<8} {c.role}{tag}")
        seeded = store.db.query_one(
            "SELECT COUNT(*) AS n FROM event WHERE type = 'meeting'")["n"]
        print(f"  scenario seeds {seeded} meetings; the 5 members also work the board")
        print("  (+1 surprise meeting injected onto Elieen mid-task, to force a pause)")

        summary = Simulation(env).run(
            on_tick=_run_hook(api),
        )

        events = _week_events(store)
        totals = {"deferred": 0, "paused": 0}
        for pid in team_with_jira.MEMBERS:
            mine = [ev for ev in events if _attended_by(ev, pid)]
            meeting_ends = {
                ev["start_tick"] + ev["duration"]
                for ev in mine if ev["type"] == "meeting"
            }
            deferred = paused = 0
            print(f"\n=== {names[pid]} — calendar ({len(mine)} blocks) ===")
            for ev in mine:
                start = format_tick(ev["start_tick"])
                end = format_tick(ev["start_tick"] + ev["duration"])
                kind = "meeting" if ev["type"] == "meeting" else "work"
                note = ""
                if kind == "work":
                    if ev["start_tick"] in meeting_ends:
                        deferred += 1
                        note = "  (deferred behind a meeting)"
                    extra = _paused_minutes(api, ev)
                    if extra:
                        paused += 1
                        note += f"  (paused & resumed +{extra}m)"
                print(f"  {start}–{end}  {kind:<7}  {_title(api, ev)}{note}")
            totals["deferred"] += deferred
            totals["paused"] += paused

        print("\n=== contention resolved by the calendar ===")
        for pid in team_with_jira.MEMBERS:
            mine = [ev for ev in events if _attended_by(ev, pid)]
            meeting_ends = {
                ev["start_tick"] + ev["duration"]
                for ev in mine if ev["type"] == "meeting"
            }
            work = [ev for ev in mine if ev["type"] == "jira_ticket"]
            deferred = sum(1 for ev in work if ev["start_tick"] in meeting_ends)
            paused_min = sum(_paused_minutes(api, ev) for ev in work)
            print(f"  {names[pid]:<8} deferred {deferred}, paused +{paused_min}m")
        print(f"  total: {totals['deferred']} work blocks deferred, "
              f"{totals['paused']} paused & resumed  "
              f"({summary.events_fired} events fired over the week)")

        env.close()


if __name__ == "__main__":
    main()
