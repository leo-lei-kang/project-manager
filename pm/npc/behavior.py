"""NPC behavior — completion-driven scheduling of coworker work.

Coworker work runs as ``jira_work`` **activities** (:mod:`pm.sim.activity`); this
module's job is deciding, from the current board state, which work to enqueue.

:class:`WorkDriver` is the single dispatch path. The driver *sweeps* the roster —
every member with nothing in flight picks their next issue per their persona —
at exactly two kinds of moment:

* **kickoff** — one sweep at week start (the driver's owner calls
  :meth:`WorkDriver.sweep` once before running the week), and
* **completion** — the driver's :meth:`WorkDriver.on_activity_done` is installed
  as the ``ActivityManager`` completion hook, so every finished activity (work,
  meeting, …) re-sweeps. One member's completion can unblock anyone, so the sweep
  covers the whole roster; the ``dispatched`` guard plus the in-flight check keep
  each person to one issue at a time and make re-sweeps idempotent.

There is no per-tick polling: between completions, nothing dispatches.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import TYPE_CHECKING

from pm.db.store import Store
from pm.jira.api import JiraApi
from pm.jira.models import Issue
from pm.npc.cast import CastMember
from pm.npc.persona import Persona, from_person
from pm.sim.events import SlackSendEvent

if TYPE_CHECKING:
    from pm.sim.activity import Activity
    from pm.sim.engine import Engine
    from pm.sim.simulation import Simulation

_TERMINAL = ("done", "cancelled")


def available_work_for(
    api: JiraApi, project_id: str, discipline: str, statuses: tuple[str, ...] = ("todo",)
) -> list[Issue]:
    """Unassigned, ready issues in ``discipline``, highest priority first.

    ``statuses`` widens the candidate pool — e.g. include ``blocked`` for a
    freestyle persona that works tickets that are not actually ready.
    """
    out: list[Issue] = []
    for status in statuses:
        out += [
            i
            for i in api.search(project_id=project_id, status=status, component=discipline)
            if i.assignee_id is None
        ]
    return out


def _persona_for(store: Store, pid: str) -> Persona:
    """The behavior persona for a person, read from the seeded ``person`` row."""
    return from_person(store.get_person(pid))


def _pickable_statuses(persona: Persona) -> tuple[str, ...]:
    """Statuses this persona picks up: freestyle takes ``blocked`` tickets too."""
    return ("todo", "blocked") if persona.work_style == "freestyle" else ("todo",)


def _in_flight(mine: list[Issue], dispatched: set[str]) -> bool:
    """Is this person actively working an issue right now?

    Only ``in_progress`` (or a just-dispatched, not-yet-terminal) issue counts as
    busy. ``in_review`` — a ``when_asked`` persona's finished-but-unclosed work —
    does not, so they keep picking up work while it awaits a standup/Slack close.
    """
    for i in mine:
        if i.status == "in_progress":
            return True
        if i.id in dispatched and i.status not in _TERMINAL and i.status != "in_review":
            return True
    return False


def _next_issue(
    api: JiraApi,
    project_id: str,
    persona: Persona,
    mine: list[Issue],
    dispatched: set[str],
    *,
    pid: str,
    now: int,
    seed: str,
    discipline: str | None = None,
) -> Issue | None:
    """The issue this person should work next under their persona, or None.

    Candidates are their own ready issues plus (for a developer hook) unassigned
    ready work in their discipline. Selection then follows the persona's
    ``work_style``: seeded-random freestyle, or priority order (preferring
    already-assigned work over unassigned).
    """
    pickable = _pickable_statuses(persona)
    assigned = [i for i in mine if i.status in pickable and i.id not in dispatched]
    unassigned = (
        [i for i in available_work_for(api, project_id, discipline, pickable) if i.id not in dispatched]
        if discipline is not None
        else []
    )
    candidates = assigned + unassigned
    if not candidates:
        return None
    # Priority <= 0 marks work the PM explicitly asked for (a Slack "pick up"
    # directive) — it overrides every work style, freestyle included.
    urgent = [i for i in candidates if i.priority <= 0]
    if urgent:
        return min(urgent, key=lambda i: (i.priority, i.id))
    if persona.work_style == "freestyle":
        rng = random.Random(f"{seed}:{pid}:{now}")
        return rng.choice(sorted(candidates, key=lambda i: i.id))
    # by_priority: prefer already-assigned work, then unassigned (both priority-ordered)
    if assigned:
        return min(assigned, key=lambda i: (i.priority, i.id))
    return unassigned[0] if unassigned else None


class WorkDriver:
    """Completion-driven NPC work: one sweep at kickoff, then a sweep per completion.

    ``members`` selects the pickup mode per entry: a plain person id works a
    pre-assigned board (match on assignee only); a :class:`CastMember` is a
    developer who also pulls unassigned ready work in their discipline
    (self-assigning it), with ``works=False`` members skipped.

    ``status_channel`` (optional) is the Slack channel an ``announces_progress``
    persona posts pickup updates to; omit it to disable those posts.
    """

    def __init__(
        self, api: JiraApi, members: list[CastMember] | list[str], project_id: str,
        *, status_channel: str | None = None,
    ) -> None:
        self.api = api
        self.members = members
        self.project_id = project_id
        self.status_channel = status_channel
        self.dispatched: set[str] = set()
        self.seed = api.repo.store.get_meta("seed", "0") or "0"

    def on_activity_done(self, engine: "Engine", activity: "Activity") -> None:
        """``ActivityManager`` completion hook: any completion may unblock anyone."""
        self.sweep(engine)

    def sweep(self, engine: "Engine") -> None:
        """Dispatch the next issue for every member with nothing in flight."""
        now = engine.clock.now()
        for member in self.members:
            is_dev = isinstance(member, CastMember)
            if is_dev and not member.works:
                continue
            pid = member.id if is_dev else member
            mine = self.api.search(project_id=self.project_id, assignee=pid)
            if _in_flight(mine, self.dispatched):
                continue
            persona = _persona_for(self.api.repo.store, pid)
            issue = _next_issue(
                self.api, self.project_id, persona, mine, self.dispatched,
                pid=pid, now=now, seed=self.seed,
                discipline=member.discipline if is_dev else None,
            )
            if issue is None:
                continue
            if issue.assignee_id != pid:
                self.api.assign_issue(issue.id, pid, actor=pid)
            self._dispatch_issue(engine, pid, persona, issue, now)

    def _dispatch_issue(
        self, engine: "Engine", pid: str, persona: Persona, issue: Issue, now: int
    ) -> None:
        """Request the ``jira_work`` activity for ``issue`` and record the dispatch.

        ``auto_close`` carries the persona's board-update policy onto the activity
        so it can finish to ``done`` (on_finish) or hold in ``in_review``
        (when_asked) without the activity reaching back into persona state.
        """
        engine.store.log_event(now, actor=pid, kind="npc.pickup",
                               payload={"issue_key": issue.id})
        engine.activities.request(
            "jira_work", [pid], issue.estimate_minutes, now,
            params={"issue_key": issue.id, "auto_close": persona.board_updates == "on_finish"},
        )
        if persona.announces_progress and self.status_channel is not None:
            engine.schedule(
                SlackSendEvent(
                    owner_id=pid,
                    start_tick=now,
                    payload={
                        "message_id": f"progress-{pid}-{issue.id}-{now}",
                        "channel_id": self.status_channel,
                        "body": f"Starting {issue.id}: {issue.title}",
                    },
                )
            )
        self.dispatched.add(issue.id)


def compose(*hooks: Callable[["Simulation"], None]) -> Callable[["Simulation"], None]:
    """Combine per-tick hooks into one (``Simulation.run`` takes a single ``on_tick``).

    Runs the hooks in order each tick — pair a PM review hook with
    :func:`pm.npc.reactions.close_and_wake_on_tick` to drive the week.
    """

    def hook(sim: "Simulation") -> None:
        for h in hooks:
            h(sim)

    return hook
