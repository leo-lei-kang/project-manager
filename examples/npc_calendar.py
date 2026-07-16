"""NPC coworkers' week on the shared clock — meetings vs. work, resolved.

    uv run python examples/npc_calendar.py

Builds the "Team with Jira" scenario (``pm.scenarios.team_with_jira``): a six-person team,
a capacity-saturated 66-task Jira board, and eleven pre-scheduled meetings (daily
standups, Alice's 1:1s, a Friday team meeting, a Wednesday ad-hoc). Then it runs
the work week completion-driven: each coworker's Jira work runs as a ``jira_work``
**activity**, and every ``MeetingEvent`` bridges into an ``in_meeting`` activity
(priority 100) that *interrupts* whatever its attendees are doing; the work
resumes afterwards with its remaining minutes intact.

After the week, a work block's span in the ``activity`` table (dispatch →
``done_tick``) exceeds its estimate exactly by the meeting minutes that cut into
it — that stretch is what this example prints, per coworker. To guarantee at
least one mid-task interruption, the run injects one surprise mid-week meeting
onto a coworker while their task is active.

Self-contained: writes to a throwaway directory.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pm.jira.api import JiraApi  # noqa: E402
from pm.jira.repository import JiraRepository  # noqa: E402
from pm.npc.behavior import WorkDriver  # noqa: E402
from pm.scenarios import team_with_jira  # noqa: E402
from pm.sim.clock import format_tick  # noqa: E402
from pm.sim.events import MeetingEvent  # noqa: E402
from pm.sim.simulation import Simulation  # noqa: E402


def _week_blocks(store) -> list[dict]:
    """Meetings (event rows) + work (activity rows), in start order.

    Each block: type, owner_id, attendees, start_tick, end_tick, payload, and —
    for work — the estimate, so a stretched span reveals the interruptions.
    """
    out = []
    for r in store.db.query_all(
            "SELECT * FROM event WHERE type = 'meeting' ORDER BY start_tick, seq"):
        payload = json.loads(r["payload_json"])
        out.append({
            "type": "meeting", "owner_id": r["owner_id"],
            "attendees": set(payload.get("attendees", [])) | {r["owner_id"]},
            "start_tick": r["start_tick"], "end_tick": r["start_tick"] + r["duration"],
            "payload": payload, "estimate": None,
        })
    for r in store.db.query_all(
            "SELECT * FROM activity WHERE kind = 'jira_work' ORDER BY created_tick, id"):
        end = r["done_tick"] if r["done_tick"] is not None else (
            r["created_tick"] + r["duration_needed"])
        attendees = json.loads(r["attendees_json"])
        out.append({
            "type": "work", "owner_id": attendees[0], "attendees": set(attendees),
            "start_tick": r["created_tick"], "end_tick": end,
            "payload": json.loads(r["params_json"]), "estimate": r["duration_needed"],
        })
    return sorted(out, key=lambda b: b["start_tick"])


def _title(api: JiraApi, block: dict) -> str:
    """The meeting's title, or the worked issue's title."""
    if block["type"] == "meeting":
        return block["payload"].get("title", "meeting")
    issue = api.get_issue(block["payload"].get("issue_key", ""))
    return issue.title if issue else block["payload"].get("issue_key", "work")


def _stretched_minutes(block: dict) -> int:
    """Minutes a work block's span exceeds its estimate (interruptions/waits)."""
    if block["type"] != "work":
        return 0
    return max(0, (block["end_tick"] - block["start_tick"]) - block["estimate"])


def _surprise_hook():
    """Per-tick hook: at Mon 12:00, drop a 30-min ad-hoc onto Elieen mid-task.

    Her design work is already ``started``, so the meeting's ``in_meeting`` bridge
    must *interrupt* it; the work resumes after the meeting with its remaining
    minutes frozen.
    """
    surprise_tick = team_with_jira.at(0, 12)
    injected = False

    def hook(sim: Simulation) -> None:
        nonlocal injected
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
        print("  (+1 surprise meeting injected onto Elieen mid-task, to force an interruption)")

        driver = WorkDriver(api, team_with_jira.MEMBERS, team_with_jira.PROJECT_ID)
        env.engine.activities.on_activity_done = driver.on_activity_done
        driver.sweep(env.engine)  # kickoff
        summary = Simulation(env).run(on_tick=_surprise_hook())

        blocks = _week_blocks(store)
        total_stretched = total_extra = 0
        for pid in team_with_jira.MEMBERS:
            mine = [b for b in blocks if pid in b["attendees"]]
            print(f"\n=== {names[pid]} — calendar ({len(mine)} blocks) ===")
            for b in mine:
                start, end = format_tick(b["start_tick"]), format_tick(b["end_tick"])
                note = ""
                extra = _stretched_minutes(b)
                if extra:
                    total_stretched += 1
                    total_extra += extra
                    note = f"  (interrupted/waited +{extra}m around meetings)"
                print(f"  {start}–{end}  {b['type']:<7}  {_title(api, b)}{note}")

        print("\n=== contention resolved by interrupt/resume ===")
        for pid in team_with_jira.MEMBERS:
            work = [b for b in blocks if b["type"] == "work" and pid in b["attendees"]]
            extra = sum(_stretched_minutes(b) for b in work)
            stretched = sum(1 for b in work if _stretched_minutes(b))
            print(f"  {names[pid]:<8} {stretched} work blocks stretched, +{extra}m total")
        print(f"  total: {total_stretched} work blocks stretched by +{total_extra}m  "
              f"({summary.events_fired} events fired over the week)")

        env.close()


if __name__ == "__main__":
    main()
