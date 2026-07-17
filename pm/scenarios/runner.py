"""Drive a scenario module's work week — the shared sim loop for `pm sim` and tests.

Completion-driven: a :class:`~pm.sim.npc.WorkDriver` sweeps the roster once at
kickoff, then re-sweeps from the two completion hooks — the ``ActivityManager``'s
``on_activity_done`` (every finished activity) and the ``Engine``'s
``on_event_done`` (standup closes; Slack effects land when the named person
*reads* the message, within the hour of the send). The per-tick ``on_tick``
carries only the optional PM ``agent_review_hook``. Runs to Fri 17:00; once
the week ends, the agent is never invoked again.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.persona import PRESETS, from_person
from pm.sim.clock import MINUTES_PER_WORKDAY, WORKDAYS
from pm.sim.events import SlackSendEvent
from pm.sim.npc import WorkDriver, compose
from pm.sim.simulation import RunSummary, Simulation

if TYPE_CHECKING:
    from pm.env.environment import Env

CXO_PUSH_TICK = 420  # 16:00 — the daily status ping lands before end of day
CXO_DM = "dm-xavier-agent"  # private DM: the engineers never see (or read) it


def schedule_cxo_pushes(env: "Env", *, sender: str = "xavier") -> None:
    """Schedule the CxO's daily status push: one private DM to the PM per workday.

    Creates the :data:`CXO_DM` channel (kind ``dm``, members = sender + the
    agent) so the channel-read fan-out stays inside the DM — no engineer ever
    gets a ``slack.read`` for it. The body names the agent ("PM"), so each
    push triggers a review (see :func:`pm.agent.hook.llm_review_hook`) and the
    PM is expected to reply in the DM. ``sender`` must be a seeded person.
    """
    env.store.db.execute(
        "INSERT INTO channel (id, name, kind) VALUES (?, ?, 'dm')", (CXO_DM, CXO_DM))
    for pid in (sender, "agent"):
        env.store.db.execute(
            "INSERT INTO channel_member (channel_id, person_id) VALUES (?, ?)",
            (CXO_DM, pid))
    for day in range(WORKDAYS):
        env.engine.schedule(SlackSendEvent(
            owner_id=sender, start_tick=day * MINUTES_PER_WORKDAY + CXO_PUSH_TICK,
            payload={"message_id": f"cxo-update-{day}", "channel_id": CXO_DM,
                     "body": "PM, please post a status update on the project: "
                             "what shipped, what's at risk, what needs unblocking."},
        ))


def _hours(minutes: int) -> str:
    """Minutes as a trimmed hour label: 4800 -> ``80h``, 390 -> ``6.5h``."""
    return f"{minutes / 60:g}h"


def _n(count: int, noun: str, plural: str | None = None) -> str:
    """Counted noun: ``1 epic``, ``2 stories``."""
    return f"{count} {noun if count == 1 else (plural or noun + 's')}"


def setup_summary(env: "Env", module: Any) -> list[str]:
    """Plain configuration lines describing what ``module``'s week starts from.

    Roster, board and calendar totals, then each epic's tickets with their
    estimates. Not simulation events — `pm sim` prints these once as a banner
    before driving; nothing lands in the ``event_log``.
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

    lines = [
        f"engineers: {len(members)} — {', '.join(members)}",
        f"board: {jira}; {informal}",
        f"calendar: {meetings}; {ooo}; {_n(channels, 'channel')}",
    ]
    for epic in by_type["epic"]:
        subtasks = [i for i in api.repo.subtree(epic.id) if i.issue_type == "task"]
        subtasks.sort(key=lambda i: int(i.id.rsplit("-", 1)[1]))
        lines.append(f"epic {epic.id} (p{epic.priority}) {epic.title} — "
                     f"{_n(len(subtasks), 'task')} / "
                     f"{_hours(sum(t.estimate_minutes for t in subtasks))}:")
        lines += [f"  {t.id}  {t.title} — {_hours(t.estimate_minutes)}" for t in subtasks]
    return lines


def drive(env: "Env", module: Any) -> RunSummary:
    """Run ``module``'s week on ``env`` (kickoff sweep + optional PM); return the summary.

    ``module`` must expose ``MEMBERS`` and ``PROJECT_ID``; it may expose
    ``agent_review_hook(env)`` to add a PM review hook and ``tick_hook(env)``
    for scenario-specific per-tick behavior (e.g. team_no_jira's notes work).
    """
    api = JiraApi(JiraRepository(env.store), env.engine)
    driver = WorkDriver(api, module.MEMBERS, module.PROJECT_ID)
    env.engine.activities.on_activity_done = driver.on_activity_done
    env.engine.on_event_done = driver.on_event_done
    review = getattr(module, "agent_review_hook", None)
    scenario_tick = getattr(module, "tick_hook", None)
    hooks = [h for h in (review(env) if review is not None else None,
                         (lambda sim: scenario_tick(env)) if scenario_tick else None)
             if h is not None]
    on_tick = hooks[0] if len(hooks) == 1 else (compose(*hooks) if hooks else None)
    sim = Simulation(env)
    driver.sweep(env.engine)  # kickoff: everyone picks their first ticket
    return sim.run(on_tick=on_tick)  # once the week ends, the agent is done
