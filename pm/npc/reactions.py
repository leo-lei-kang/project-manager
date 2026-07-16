"""Reactive NPC hooks — one per event type.

An event's own effect lives on its :class:`~pm.sim.events.Event` subclass; this
module adds the *reactive* layer on top: when an event completes, a coworker may
respond by scheduling follow-up work. :data:`REACTIONS` maps **every**
:class:`~pm.sim.events.EventType` to a handler, :func:`react` dispatches, and
:func:`react_on_tick` is a ``Simulation.run(on_tick=…)`` driver that fires the
reactions for events completed on the current tick.

The flagship reaction is the Jira work cascade (finish an issue → pick up your next
ready one). The remaining hooks are registered placeholders documenting their
intended behaviour.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from pm.npc.persona import from_person
from pm.sim.events import EventType, JiraTicketEvent

_TERMINAL_STATUSES = ("done", "cancelled")

if TYPE_CHECKING:
    from pm.jira.api import JiraApi
    from pm.npc.behavior import WorkDriver
    from pm.sim.engine import Engine
    from pm.sim.events import Event
    from pm.sim.simulation import Simulation

Reaction = Callable[["Engine", "Event"], None]

# Event types whose reactions close a ``when_asked`` persona's finished work,
# distinct from the work cascade — see :func:`close_reactions_on_tick`.
_CLOSE_EVENTS = (EventType.MEETING, EventType.SLACK_SEND)


def _jira(engine: "Engine") -> "JiraApi":
    # Function-local: pm.jira.api imports the engine, so a module-level import here
    # would form a reactions → jira.api → engine cycle.
    from pm.jira.api import JiraApi
    from pm.jira.repository import JiraRepository

    return JiraApi(JiraRepository(engine.store), engine)


def _noop(engine: "Engine", event: "Event") -> None:
    """No reaction (documented placeholder)."""
    return None


def _close_in_review(api: "JiraApi", person_id: str, *, trigger: str) -> None:
    """Mark this person's finished-but-unclosed (``in_review``) issues ``done``."""
    store = api.repo.store
    for issue in api.search(assignee=person_id, status="in_review"):
        store.log_event(store.get_tick(), actor=person_id, kind="npc.close_review",
                        payload={"issue_key": issue.id, "trigger": trigger})
        api.transition_issue(issue.id, "done", actor=person_id)


def _on_jira_ticket_done(engine: "Engine", event: "Event") -> None:
    """A coworker who just finished a Jira ticket picks up their next ready one.

    Carries the owner's board-update policy onto the next ticket so a ``when_asked``
    persona keeps parking finished work in ``in_review`` in the reaction-driven mode.
    """
    api = _jira(engine)
    ready = api.search(assignee=event.owner_id, status="todo")
    if not ready:
        return
    issue = ready[0]
    persona = from_person(engine.store.get_person(event.owner_id))
    engine.store.log_event(engine.clock.now(), actor=event.owner_id,
                           kind="npc.pickup", payload={"issue_key": issue.id})
    engine.schedule(
        JiraTicketEvent(
            owner_id=event.owner_id,
            start_tick=engine.clock.now(),
            duration=issue.estimate_minutes,
            payload={"issue_key": issue.id, "auto_close": persona.board_updates == "on_finish"},
        )
    )


def _on_meeting_done(engine: "Engine", event: "Event") -> None:
    """A standup ending prompts its attendees to close their pending (in_review) work."""
    if event.payload.get("kind") != "standup":
        return
    api = _jira(engine)
    for pid in event.payload.get("attendees", []):
        _close_in_review(api, pid, trigger="standup")


def _on_slack_send(engine: "Engine", event: "Event") -> None:
    """A Slack message naming a person prompts them to close their pending work.

    "Named" is a case-insensitive substring match of the person's ``name`` or ``id``
    in the message body — deterministic, no model in the loop.

    A *directive* message — one containing the phrase "pick up" — additionally
    bumps every issue key it names to priority 0, the "explicitly asked by the
    PM" level that even a freestyle persona works first (see
    :func:`pm.npc.behavior._next_issue`). A mere mention of a key (a status
    highlight) steers nothing.
    """
    body = event.payload.get("body", "").lower()
    if not body:
        return
    api = _jira(engine)
    for person in engine.store.list_people():
        if person.name.lower() in body or person.id.lower() in body:
            _close_in_review(api, person.id, trigger="slack")
    if "pick up" in body:
        for key in re.findall(r"\b[a-z]+-\d+\b", body):
            issue = api.repo.get_issue(key.upper())
            if issue is not None and issue.status not in _TERMINAL_STATUSES:
                engine.store.log_event(
                    engine.clock.now(), actor=issue.assignee_id or event.owner_id,
                    kind="npc.priority_bump", payload={"issue_key": issue.id})
                api.set_priority(issue.id, 0, actor=event.owner_id)


# A hook for every event type. The Jira cascade and the standup/Slack closes are
# live; the rest are registered placeholders (no-ops) with their intent noted.
REACTIONS: dict[EventType, Reaction] = {
    EventType.EMAIL_SEND: _noop,      # TODO: recipient may reply on the slower email cadence
    EventType.EMAIL_READ: _noop,      # awareness only — nothing to react to
    EventType.SLACK_SEND: _on_slack_send,  # naming a person closes their in_review work
    EventType.SLACK_READ: _noop,      # awareness only — nothing to react to
    EventType.JIRA_TICKET: _on_jira_ticket_done,  # finish → pick up next ready assigned ticket
    EventType.MEETING: _on_meeting_done,  # standup end → attendees close in_review work
    EventType.OOO: _noop,             # TODO: on return, the person picks up their backlog
}


def react(engine: "Engine", event: "Event") -> None:
    """Dispatch the reaction for a completed ``event``."""
    REACTIONS.get(event.type, _noop)(engine, event)


def react_on_tick(sim: "Simulation") -> None:
    """``Simulation.run`` hook: fire reactions for every event completed this tick."""
    now = sim.clock.now()
    for event in sim.store.events_done_at(now):
        react(sim.engine, event)


def close_reactions_on_tick(sim: "Simulation") -> None:
    """``Simulation.run`` hook: fire only the standup/Slack close reactions.

    Excludes the work cascade so this can be composed with a pickup hook (which
    already re-picks after each finish) without double-dispatching work.
    """
    now = sim.clock.now()
    for event in sim.store.events_done_at(now):
        if event.type in _CLOSE_EVENTS:
            react(sim.engine, event)


def close_and_wake_on_tick(driver: "WorkDriver") -> Callable[["Simulation"], None]:
    """Build the ``Simulation.run`` hook for a completion-driven week.

    Fires the standup/Slack close reactions for events completed this tick, then —
    only if any fired — sweeps the :class:`~pm.npc.behavior.WorkDriver`, so a close
    that unparks ``in_review`` work (unblocking a dependent) dispatches the
    dependent immediately. Work chaining itself needs no tick hook: it rides the
    driver's ``on_activity_done`` completion hook.
    """

    def hook(sim: "Simulation") -> None:
        now = sim.clock.now()
        fired = False
        for event in sim.store.events_done_at(now):
            if event.type in _CLOSE_EVENTS:
                react(sim.engine, event)
                fired = True
        if fired:
            driver.sweep(sim.engine)

    return hook
