"""NPC behavior — scheduling coworker work on the Jira board.

Coworker behaviour is carried by the durative event types in :mod:`pm.sim.events`
(each applies its own effect on start/done); this module's job is *scheduling* —
deciding, from the current board state, which work to enqueue.

Two per-tick ``Simulation.run`` hooks give coworker NPCs agency over simulated
time, differing only in how they pick an issue:

* :func:`dev_pickup_hook` — developers take the next ready issue in their
  discipline (assigning it to themselves if needed). Managers/execs
  (``works=False``) create and assign rather than implement, so they are skipped.
* :func:`assignee_pickup_hook` — for a pre-assigned board, each coworker works the
  next ready issue already assigned to them.

Both then work the issue to completion via an
:class:`~pm.sim.events.JiraTicketEvent` spanning its estimate; a ``dispatched``
guard plus the in-flight check keep each person to one issue at a time.
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
from pm.sim.events import JiraTicketEvent, SlackSendEvent

if TYPE_CHECKING:
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


def _dispatch_work(
    sim: "Simulation", owner_id: str, issue: Issue, dispatched: set[str], persona: Persona,
    status_channel: str | None = None,
) -> None:
    """Schedule the work event for ``issue`` and record it as dispatched.

    ``auto_close`` carries the persona's board-update policy onto the event so it
    can finish to ``done`` (on_finish) or hold in ``in_review`` (when_asked)
    without the event reaching back into persona state.

    An ``announces_progress`` persona (see :data:`~pm.npc.persona.PERFECT`) also posts
    a Slack status update to ``status_channel`` — raising the team's visibility. Skipped
    when no channel is wired in.
    """
    now = sim.clock.now()
    sim.schedule(
        JiraTicketEvent(
            owner_id=owner_id,
            start_tick=now,
            duration=issue.estimate_minutes,
            payload={"issue_key": issue.id, "auto_close": persona.board_updates == "on_finish"},
        )
    )
    if persona.announces_progress and status_channel is not None:
        sim.schedule(
            SlackSendEvent(
                owner_id=owner_id,
                start_tick=now,
                payload={
                    "message_id": f"progress-{owner_id}-{issue.id}-{now}",
                    "channel_id": status_channel,
                    "body": f"Starting {issue.id}: {issue.title}",
                },
            )
        )
    dispatched.add(issue.id)


def dev_pickup_hook(
    api: JiraApi, roster: list[CastMember], project_id: str,
    *, status_channel: str | None = None,
) -> Callable[["Simulation"], None]:
    """Build a per-tick hook: idle devs take & work the next issue in their discipline.

    ``status_channel`` (optional) is the Slack channel an ``announces_progress`` persona
    posts pickup updates to; omit it to disable those posts.
    """
    dispatched: set[str] = set()
    seed = api.repo.store.get_meta("seed", "0") or "0"

    def hook(sim: "Simulation") -> None:
        now = sim.clock.now()
        for spec in roster:
            if not spec.works:
                continue
            mine = api.search(project_id=project_id, assignee=spec.id)
            if _in_flight(mine, dispatched):
                continue
            persona = _persona_for(api.repo.store, spec.id)
            issue = _next_issue(
                api, project_id, persona, mine, dispatched,
                pid=spec.id, now=now, seed=seed, discipline=spec.discipline,
            )
            if issue is None:
                continue
            if issue.assignee_id != spec.id:
                api.assign_issue(issue.id, spec.id, actor=spec.id)
            _dispatch_work(sim, spec.id, issue, dispatched, persona, status_channel)

    return hook


def assignee_pickup_hook(
    api: JiraApi, person_ids: list[str], project_id: str,
    *, status_channel: str | None = None,
) -> Callable[["Simulation"], None]:
    """Build a per-tick hook for a pre-assigned board: each person works their own todo.

    Matches on assignee (not discipline), so it drives boards whose issues are
    assigned up front and carry no component; dependencies cascade as blockers finish.
    ``status_channel`` (optional) is the Slack channel an ``announces_progress`` persona
    posts pickup updates to; omit it to disable those posts.
    """
    dispatched: set[str] = set()
    seed = api.repo.store.get_meta("seed", "0") or "0"

    def hook(sim: "Simulation") -> None:
        now = sim.clock.now()
        for pid in person_ids:
            mine = api.search(project_id=project_id, assignee=pid)
            if _in_flight(mine, dispatched):
                continue
            persona = _persona_for(api.repo.store, pid)
            issue = _next_issue(
                api, project_id, persona, mine, dispatched, pid=pid, now=now, seed=seed,
            )
            if issue is not None:
                _dispatch_work(sim, pid, issue, dispatched, persona, status_channel)

    return hook


def compose(*hooks: Callable[["Simulation"], None]) -> Callable[["Simulation"], None]:
    """Combine per-tick hooks into one (``Simulation.run`` takes a single ``on_tick``).

    Runs the hooks in order each tick — pair a pickup hook with
    :func:`pm.npc.reactions.close_reactions_on_tick` to drive dispatch and the
    standup/Slack closes together.
    """

    def hook(sim: "Simulation") -> None:
        for h in hooks:
            h(sim)

    return hook
