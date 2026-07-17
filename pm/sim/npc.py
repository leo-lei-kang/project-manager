"""The NPC component — coworker behavior and reactions, one seam into the sim.

Everything an NPC *decides* lives here, in two halves of one component:

**Behavior** — completion-driven scheduling of coworker work. Coworker work runs
as ``jira_work`` **activities** (:mod:`pm.sim.activity`); :class:`WorkDriver` is
the single dispatch path. The driver *sweeps* the roster — every member with
nothing in flight picks their next issue per their persona — at exactly two
kinds of moment:

* **kickoff** — one sweep at week start (the driver's owner calls
  :meth:`WorkDriver.sweep` once before running the week), and
* **completion** — the driver's :meth:`WorkDriver.on_activity_done` is installed
  as the ``ActivityManager`` completion hook, so every finished activity (work,
  meeting, …) re-sweeps. One member's completion can unblock anyone, so the sweep
  covers the whole roster; the ``dispatched`` guard plus the in-flight check keep
  each person to one issue at a time and make re-sweeps idempotent.

There is no per-tick polling: between completions, nothing dispatches.

**Reactions** — how completed *events* feed back into behavior. :data:`REACTIONS`
maps every :class:`~pm.sim.events.EventType` to a handler; the live ones are the
Slack read/send pair (a send schedules a delayed read for each person named; the
*read* closes the reader's parked ``in_review`` work and takes "pick up"
directives), the standup close, and the event-path Jira cascade the procedural
generator uses. :meth:`WorkDriver.on_event_done` is the ``Engine``
event-completion hook a driver's owner installs: it fires the close reaction,
then re-sweeps so a close that unblocks a dependent dispatches it immediately.

The component interacts with exactly two things — **activities** (request,
``on_activity_done``) and **events** (schedule, ``on_event_done``); it never
touches the ``Simulation`` loop.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from pm.db.store import Store
from pm.jira.api import JiraApi
from pm.jira.models import Issue
from pm.jira.repository import JiraRepository
from pm.npc.cast import CastMember
from pm.npc.persona import Persona, from_person
from pm.sim.activity import ACTIVITY_KINDS
from pm.sim.events import EventType, JiraTicketEvent, SlackReadEvent, SlackSendEvent

if TYPE_CHECKING:
    from pm.sim.activity import Activity
    from pm.sim.engine import Engine
    from pm.sim.events import Event
    from pm.sim.simulation import Simulation

_TERMINAL = ("done", "cancelled")

# One notch above normal jira_work: a PM-directed ticket interrupts the ticket
# being worked (which resumes afterwards) but still yields to meetings/OOO.
_URGENT_ACTIVITY_PRIORITY = ACTIVITY_KINDS["jira_work"].priority + 1

Reaction = Callable[["Engine", "Event"], None]

# Event types whose reactions steer the board — the standup/read closes and the
# read-time directive bump (SLACK_SEND only schedules the reads) — followed by a
# re-sweep; distinct from the work cascade. See :meth:`WorkDriver.on_event_done`.
_CLOSE_EVENTS = (EventType.MEETING, EventType.SLACK_SEND, EventType.SLACK_READ)


# --------------------------------------------------------------------------- #
# Behavior: picking and dispatching work.
# --------------------------------------------------------------------------- #
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


def _ticket_brief(repo: JiraRepository, issue: Issue) -> dict:
    """One pickup-log line item: key, priority, status, blocked/blocking flags.

    ``blocked`` also covers an issue being worked despite open deps (a freestyle
    pick); ``blocking`` counts the open dependents that finishing it would unblock.
    """
    blocked = issue.status == "blocked" or any(
        (dep := repo.get_issue(k)) is not None and dep.status not in _TERMINAL
        for k in issue.depends_on
    )
    blocking = sum(1 for d in repo.dependents(issue.id) if d.status not in _TERMINAL)
    return {"key": issue.id, "priority": issue.priority, "status": issue.status,
            "blocked": blocked, "blocking": blocking}


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

    Candidates are their own ready issues plus (for a developer roster) unassigned
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

    def on_event_done(self, engine: "Engine", event: "Event") -> None:
        """``Engine`` event-completion hook: fire the close reaction, then re-sweep.

        A standup ending, or a person reading a Slack message that names them,
        closes their parked ``in_review`` work — which can unblock a dependent —
        and a read directive bumps its ticket, so the sweep dispatches (or
        preempts) immediately. Work chaining itself rides
        :meth:`on_activity_done` — the component's only seams are the two
        completion hooks (events and activities); nothing touches the
        ``Simulation`` loop.
        """
        if event.type not in _CLOSE_EVENTS:
            return
        react(engine, event)
        self.sweep(engine)

    def sweep(self, engine: "Engine") -> None:
        """Dispatch the next issue for every member with nothing in flight."""
        now = engine.clock.now()
        for member in self.members:
            is_dev = isinstance(member, CastMember)
            if is_dev and not member.works:
                continue
            pid = member.id if is_dev else member
            discipline = member.discipline if is_dev else None
            mine = self.api.search(project_id=self.project_id, assignee=pid)
            if _in_flight(mine, self.dispatched):
                self._preempt_if_directed(engine, pid, mine, now, discipline)
                continue
            persona = _persona_for(self.api.repo.store, pid)
            issue = _next_issue(
                self.api, self.project_id, persona, mine, self.dispatched,
                pid=pid, now=now, seed=self.seed, discipline=discipline,
            )
            if issue is None:
                continue
            if issue.assignee_id != pid:
                self.api.assign_issue(issue.id, pid, actor=pid)
            self._dispatch_issue(engine, pid, persona, issue, now, discipline=discipline)

    def _preempt_if_directed(
        self, engine: "Engine", pid: str, mine: list[Issue], now: int,
        discipline: str | None,
    ) -> None:
        """A PM "pick up" directive (priority <= 0) preempts the ticket being worked.

        Dispatches the directed issue just above normal ``jira_work`` activity
        priority, so the ``ActivityManager`` interrupts the current ticket's
        activity; it resumes with its remaining time once the directed one is done.
        """
        current = engine.activities.started_for(pid)
        if current is None or current.kind != "jira_work":
            return
        persona = _persona_for(self.api.repo.store, pid)
        issue = _next_issue(
            self.api, self.project_id, persona, mine, self.dispatched,
            pid=pid, now=now, seed=self.seed, discipline=discipline,
        )
        if issue is None or issue.priority > 0:
            return
        if issue.assignee_id != pid:
            self.api.assign_issue(issue.id, pid, actor=pid)
        self._dispatch_issue(engine, pid, persona, issue, now, discipline=discipline,
                             activity_priority=_URGENT_ACTIVITY_PRIORITY)

    def _dispatch_issue(
        self, engine: "Engine", pid: str, persona: Persona, issue: Issue, now: int,
        *, discipline: str | None = None, activity_priority: int | None = None,
    ) -> None:
        """Request the ``jira_work`` activity for ``issue`` and record the dispatch.

        The pickup log carries the person's whole plate — every open assigned
        ticket plus the unassigned discipline pool, as :func:`_ticket_brief`
        dicts — so the log shows what the pick was chosen over. ``mine`` is
        recomputed here: the sweep's list predates ``assign_issue``.

        ``auto_close`` carries the persona's board-update policy onto the activity
        so it can finish to ``done`` (on_finish) or hold in ``in_review``
        (when_asked) without the activity reaching back into persona state.
        """
        mine = self.api.search(project_id=self.project_id, assignee=pid)
        open_tickets = [_ticket_brief(self.api.repo, i) for i in mine
                        if i.status not in _TERMINAL]
        pool = (
            [_ticket_brief(self.api.repo, i)
             for i in available_work_for(self.api, self.project_id, discipline,
                                         _pickable_statuses(persona))]
            if discipline is not None else []
        )
        engine.store.log_event(now, actor=pid, kind="npc.pickup",
                               payload={"issue_key": issue.id,
                                        "open": open_tickets, "pool": pool})
        engine.activities.request(
            "jira_work", [pid], issue.estimate_minutes, now,
            params={"issue_key": issue.id, "auto_close": persona.board_updates == "on_finish"},
            priority=activity_priority,
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

    Runs the hooks in order each tick — for composing PM review hooks; the NPC
    component itself needs no tick hook.
    """

    def hook(sim: "Simulation") -> None:
        for h in hooks:
            h(sim)

    return hook


# --------------------------------------------------------------------------- #
# Reactions: one handler per completed event type.
# --------------------------------------------------------------------------- #
def _jira(engine: "Engine") -> "JiraApi":
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


def _named(person, low: str) -> bool:
    """Is ``person`` addressed by the (lowercased) message body?

    A case-insensitive substring match of the person's ``name`` or ``id`` —
    deterministic, no model in the loop.
    """
    return person.name.lower() in low or person.id.lower() in low


def _on_slack_send(engine: "Engine", event: "Event") -> None:
    """A channel message: everyone in the channel reads it within the hour.

    Every channel member except the sender (and agents — the PM reads the
    channel via its ``read_slack`` tool) gets a
    :class:`~pm.sim.events.SlackReadEvent` at a seeded-random 1–60 minutes
    later. A channel with no ``channel_member`` rows is open — everyone reads;
    a membered channel (a DM, say the CxO's status pushes to the PM) fans out
    to its members only. The message's *effects* fire only when a reader it
    addresses reads it (see :func:`_on_slack_read`); everyone else's read is
    awareness only. A read that would land during a meeting or OOO yields past
    it at schedule time (see :func:`pm.sim.calendar.reserve`). A read
    scheduled past week end never fires — a directive sent in the last hour
    can go unread.
    """
    body = event.payload.get("body", "")
    if not body:
        return
    now = engine.clock.now()
    seed = engine.store.get_meta("seed", "0") or "0"
    members = engine.store.channel_members(event.payload.get("channel_id", ""))
    for person in engine.store.list_people():
        if person.id == event.owner_id or person.is_agent:
            continue
        if members and person.id not in members:
            continue  # membered channel (e.g. a DM): outsiders never read it
        delay = random.Random(f"{seed}:{person.id}:{now}").randint(1, 60)
        engine.schedule(SlackReadEvent(
            owner_id=person.id, initiator_id=event.owner_id,
            start_tick=now + delay,
            payload={"channel_id": event.payload.get("channel_id", ""),
                     "message_id": event.payload.get("message_id", ""),
                     "body": body},
        ))


def _on_slack_read(engine: "Engine", event: "Event") -> None:
    """Reading a message that names you: close your parked work, take directives.

    A reader the body does not address takes no action (awareness only). A
    named reader closes their own finished-but-unclosed (``in_review``) issues,
    and a *directive* body — one containing the phrase "pick up" — additionally
    bumps the named issue keys **assigned to the reader (or unassigned)** to
    priority 0, the "explicitly asked by the PM" level that even a freestyle
    persona works first (see :func:`_next_issue`) and that preempts the
    assignee's in-progress ticket (see :meth:`WorkDriver._preempt_if_directed`).
    One message can carry directives for several people; each takes effect when
    its addressee reads. A mere mention of a key (a status highlight) steers
    nothing.
    """
    body = event.payload.get("body", "").lower()
    reader = engine.store.get_person(event.owner_id)
    if reader is None or not _named(reader, body):
        return  # not addressed: the read is awareness only
    api = _jira(engine)
    _close_in_review(api, event.owner_id, trigger="slack")
    if "pick up" in body:
        for key in re.findall(r"\b[a-z]+-\d+\b", body):
            issue = api.repo.get_issue(key.upper())
            if (issue is not None and issue.status not in _TERMINAL
                    and issue.assignee_id in (None, event.owner_id)):
                api.set_priority(issue.id, 0, actor=event.owner_id)


# A hook for every event type. The standup/Slack closes and the event-path Jira
# cascade are live; the rest are registered placeholders (no-ops) with intent noted.
REACTIONS: dict[EventType, Reaction] = {
    EventType.EMAIL_SEND: _noop,      # TODO: recipient may reply on the slower email cadence
    EventType.EMAIL_READ: _noop,      # awareness only — nothing to react to
    EventType.SLACK_SEND: _on_slack_send,  # everyone reads the channel within the hour
    EventType.SLACK_READ: _on_slack_read,  # a named reader closes work / takes directives
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
