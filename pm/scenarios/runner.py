"""Drive a scenario module's work week — the shared sim loop for `pm sim` and tests.

Completion-driven: a :class:`~pm.sim.npc.WorkDriver` sweeps the roster once at
kickoff, then re-sweeps from the two completion hooks — the ``ActivityManager``'s
``on_activity_done`` (every finished activity) and the ``Engine``'s
``on_event_done`` (standup closes; Slack effects land when the named person
*reads* the message, within the hour of the send). The per-tick ``on_tick``
carries only the optional PM ``agent_review_hook``. Runs to Fri 17:00, then
fires one final PM close-out for work that finished on the last tick.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.persona import PRESETS, from_person
from pm.sim.npc import WorkDriver
from pm.sim.simulation import RunSummary, Simulation

if TYPE_CHECKING:
    from pm.env.environment import Env


def _hours(minutes: int) -> str:
    """Minutes as a trimmed hour label: 4800 -> ``80h``, 390 -> ``6.5h``."""
    return f"{minutes / 60:g}h"


def _n(count: int, noun: str, plural: str | None = None) -> str:
    """Counted noun: ``1 epic``, ``2 stories``."""
    return f"{count} {noun if count == 1 else (plural or noun + 's')}"


def setup_summary(env: "Env", module: Any) -> list[str]:
    """Plain configuration lines describing what ``module``'s week starts from.

    Not simulation events — `pm sim` prints these once as a banner before
    driving; nothing lands in the ``event_log``.
    """
    api = JiraApi(JiraRepository(env.store), env.engine)
    members = []
    for m in module.MEMBERS:
        pid = getattr(m, "id", m)
        person = env.store.get_person(pid)
        persona = from_person(person)
        name = next((n for n, p in PRESETS.items() if p == persona), "custom")
        discipline = person.persona.get("discipline", "") if person else ""
        members.append(f"{pid} ({name}{', ' + discipline if discipline else ''})")

    by_type = {t: api.search(project_id=module.PROJECT_ID, issue_type=t)
               for t in ("epic", "story", "task")}
    tasks = by_type["task"]
    jira = (f"jira {_n(len(tasks), 'task')} / "
            f"{_hours(sum(t.estimate_minutes for t in tasks))} "
            f"({_n(len(by_type['epic']), 'epic')}, {_n(len(by_type['story']), 'story', 'stories')})"
            if any(by_type.values()) else "jira: none")
    pending_meetings = env.store.db.query_all(
        "SELECT duration, payload_json FROM event WHERE type = 'meeting' AND status = 'pending'")
    # Distinct ids: later standups re-mention tasks the kickoff established.
    planned = len({t["id"] for r in pending_meetings
                   for t in json.loads(r["payload_json"]).get("tasks", [])})
    informal = f"informal tasks: {len(env.store.list_tasks())} seeded, {planned} planned in meetings"

    meetings = (f"{_n(len(pending_meetings), 'meeting')} / "
                f"{_hours(sum(r['duration'] for r in pending_meetings))}"
                if pending_meetings else "no meetings")
    pending_ooo = env.store.db.query_all(
        "SELECT duration FROM event WHERE type = 'ooo' AND status = 'pending'")
    ooo = (f"{len(pending_ooo)} ooo / {_hours(sum(r['duration'] for r in pending_ooo))}"
           if pending_ooo else "no ooo")
    channels = env.store.db.query_one("SELECT COUNT(*) AS n FROM channel")["n"]

    return [
        f"engineers: {len(members)} — {', '.join(members)}",
        f"board: {jira}; {informal}",
        f"calendar: {meetings}; {ooo}; {_n(channels, 'channel')}",
    ]


def drive(env: "Env", module: Any) -> RunSummary:
    """Run ``module``'s week on ``env`` (kickoff sweep + optional PM); return the summary.

    ``module`` must expose ``MEMBERS`` and ``PROJECT_ID``; it may expose
    ``agent_review_hook(env)`` to add a PM review hook.
    """
    api = JiraApi(JiraRepository(env.store), env.engine)
    driver = WorkDriver(api, module.MEMBERS, module.PROJECT_ID)
    env.engine.activities.on_activity_done = driver.on_activity_done
    env.engine.on_event_done = driver.on_event_done
    review = getattr(module, "agent_review_hook", None)
    review_hook = review(env) if review is not None else None
    sim = Simulation(env)
    driver.sweep(env.engine)  # kickoff: everyone picks their first ticket
    summary = sim.run(on_tick=review_hook)
    if review_hook is not None:
        review_hook(sim)  # week-end close-out for work finishing on the final tick
    return summary
