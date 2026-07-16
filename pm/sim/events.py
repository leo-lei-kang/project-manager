"""Durative events — the unit of asynchronous activity in the simulation.

Every activity in the world is one :class:`Event` object with a uniform lifecycle:

    pending --start()--> active --done()--> done

and a single API shared by every type:

    * ``start(engine)``            — begin the activity (onset effect), at ``start_tick``
    * ``remaining(now)``           — "check remaining time" until it finishes
    * ``done(engine)``             — complete the activity (completion effect)

There is exactly **one row per event** (see the ``event`` table); completion is a status
flip on that same row, not a separate record. Behaviour lives on the Event subclass; the
engine rehydrates the right subclass from a DB row via :data:`EVENT_REGISTRY`.

Only these event types exist (nothing else may be scheduled):

    email.send, email.read, slack.send, slack.read, jira_ticket, meeting
    (kind ∈ standup | adhoc | 1on1 | team), ooo.

Instantaneous events have ``duration == 0`` and start *and* finish in the same tick.
Durative events (task work, meetings) span ``duration`` ticks.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING

from pm.world.models import Email, Meeting, Message, Task, Transcript

if TYPE_CHECKING:  # avoid runtime import cycles; used only for typing
    from pm.jira.api import JiraApi
    from pm.jira.models import IssueStatus
    from pm.sim.engine import Engine


class EventStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    CANCELLED = "cancelled"


class EventType(str, Enum):
    EMAIL_SEND = "email.send"
    EMAIL_READ = "email.read"
    SLACK_SEND = "slack.send"
    SLACK_READ = "slack.read"
    JIRA_TICKET = "jira_ticket"
    MEETING = "meeting"
    OOO = "ooo"


class Event(ABC):
    """Base class: shared fields + the uniform start/remaining/done lifecycle."""

    #: Set by each concrete subclass; also the persisted ``type`` string.
    type: EventType

    def __init__(
        self,
        *,
        owner_id: str,
        start_tick: int,
        duration: int = 0,
        payload: dict | None = None,
        id: int | None = None,
        seq: int = 0,
        status: EventStatus = EventStatus.PENDING,
        created_tick: int = 0,
        done_tick: int | None = None,
        initiator_id: str | None = None,
    ) -> None:
        if duration < 0:
            raise ValueError(f"event duration must be non-negative, got {duration}")
        self.id = id
        self.owner_id = owner_id  # who carries out / is responsible for the event
        self.initiator_id = initiator_id  # who triggered it (may differ from owner)
        self.start_tick = start_tick
        self.duration = duration
        self.payload = payload or {}
        self.seq = seq
        self.status = status
        self.created_tick = created_tick
        self.done_tick = done_tick

    # -- uniform API ---------------------------------------------------------

    def remaining(self, now: int) -> int:
        """Ticks left until this event finishes (0 once due to complete)."""
        return max(0, (self.start_tick + self.duration) - now)

    def should_start(self, now: int) -> bool:
        return self.status is EventStatus.PENDING and now >= self.start_tick

    def is_finished(self, now: int) -> bool:
        return self.status is EventStatus.ACTIVE and self.remaining(now) == 0

    def start(self, engine: "Engine") -> None:
        """Begin the activity: run the onset effect, mark active, persist + log."""
        now = engine.clock.now()
        self._on_start(engine)
        self.status = EventStatus.ACTIVE
        engine.store.upsert_event(self)
        engine.store.log_event(
            now, actor=self.owner_id, kind=f"{self.type.value}.start", payload=self.payload
        )

    def done(self, engine: "Engine") -> None:
        """Complete the activity: run the completion effect, mark done, persist + log."""
        now = engine.clock.now()
        self._on_done(engine)
        self.status = EventStatus.DONE
        self.done_tick = now
        engine.store.upsert_event(self)
        engine.store.log_event(
            now, actor=self.owner_id, kind=self._done_kind(), payload=self.payload
        )

    def _done_kind(self) -> str:
        """The log kind for the done-phase; overridden where the pair is named."""
        return f"{self.type.value}.done"

    # -- subclass hooks ------------------------------------------------------

    @abstractmethod
    def _on_start(self, engine: "Engine") -> None: ...

    @abstractmethod
    def _on_done(self, engine: "Engine") -> None: ...

    # -- persistence helpers -------------------------------------------------

    @classmethod
    def from_row(cls, r) -> "Event":
        """Rehydrate the correct subclass from an ``event`` table row."""
        etype = EventType(r["type"])
        subcls = EVENT_REGISTRY[etype]
        return subcls(
            id=r["id"],
            owner_id=r["owner_id"],
            initiator_id=r["initiator_id"],
            start_tick=r["start_tick"],
            duration=r["duration"],
            seq=r["seq"],
            status=EventStatus(r["status"]),
            payload=json.loads(r["payload_json"]),
            created_tick=r["created_tick"],
            done_tick=r["done_tick"],
        )

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(id={self.id}, owner={self.owner_id!r}, "
            f"start={self.start_tick}, dur={self.duration}, status={self.status.value})"
        )


# --------------------------------------------------------------------------- #
# Concrete events. Each sets `type` and implements the onset/completion effects.
# All world mutation goes through engine.store (the single writer).
# --------------------------------------------------------------------------- #


class EmailSendEvent(Event):
    """Send an email; it lands in recipients' inboxes once the delivery latency
    elapses. payload: email fields."""

    type = EventType.EMAIL_SEND

    def _on_start(self, engine: "Engine") -> None:
        pass  # in flight during `duration`

    def _on_done(self, engine: "Engine") -> None:
        p = self.payload
        engine.store.add_email(
            Email(
                id=p["email_id"],
                thread_id=p["thread_id"],
                sender_id=self.owner_id,
                subject=p.get("subject", ""),
                body=p.get("body", ""),
                sent_tick=engine.clock.now(),
                to=p.get("to", []),
                cc=p.get("cc", []),
            )
        )


class EmailReadEvent(Event):
    """A recipient reads a delivered email — drives NPC awareness. payload: email_id.

    The read itself is the recorded outcome (event row + log); there is no separate
    world table for it, so both hooks are no-ops beyond the base logging.
    """

    type = EventType.EMAIL_READ

    def _on_start(self, engine: "Engine") -> None:
        pass

    def _on_done(self, engine: "Engine") -> None:
        pass


class SlackSendEvent(Event):
    """A chat message becomes visible on completion (models send/typing latency)."""

    type = EventType.SLACK_SEND

    def _on_start(self, engine: "Engine") -> None:
        pass

    def _on_done(self, engine: "Engine") -> None:
        p = self.payload
        engine.store.add_message(
            Message(
                id=p["message_id"],
                channel_id=p["channel_id"],
                sender_id=self.owner_id,
                body=p.get("body", ""),
                sent_tick=engine.clock.now(),
            )
        )


class SlackReadEvent(Event):
    """A coworker reads a channel — drives NPC awareness. payload: channel_id.

    The read itself is the recorded outcome (event row + log); there is no separate
    world table for it, so both hooks are no-ops beyond the base logging.
    """

    type = EventType.SLACK_READ

    def _on_start(self, engine: "Engine") -> None:
        pass

    def _on_done(self, engine: "Engine") -> None:
        pass


class OOOEvent(Event):
    """A person is out of office for ``duration`` ticks (a few hours to a few days).

    Durative; ``owner_id`` is the person out. Both hooks are no-ops — the *effect*
    is that the person is booked out on their calendar for the span (OOO is a
    top-priority occupying type in :mod:`pm.sim.calendar`), so meetings and work
    scheduled for them during the window are shifted to after it. Optional payload:
    ``reason``.
    """

    type = EventType.OOO

    def _on_start(self, engine: "Engine") -> None:
        # Bridge into the activity system: an ``ooo`` activity (priority 200, no
        # effects) occupies the person so being out preempts any activity work.
        if self.duration > 0:
            engine.activities.request(
                "ooo", [self.owner_id], self.duration, engine.clock.now(),
                params={"event_id": self.id},
            )

    def _on_done(self, engine: "Engine") -> None:
        pass


class JiraTicketEvent(Event):
    """A coworker works a *Jira* ticket over its estimate. start → in_progress;
    done → log the remaining work then transition to done (which unblocks any
    dependents). payload: issue_key.

    Bridges the sim to the separate ``pm.jira`` subsystem. The imports are
    function-local: ``pm.jira.api`` imports the engine, so a module-level import
    here would form an events → jira.api → engine → events cycle. Mutations reuse
    ``JiraApi`` so all transition + derived-blocked rules stay in one place.
    """

    type = EventType.JIRA_TICKET

    def _api(self, engine: "Engine") -> "JiraApi":
        from pm.jira.api import JiraApi
        from pm.jira.repository import JiraRepository

        return JiraApi(JiraRepository(engine.store), engine)

    def _on_start(self, engine: "Engine") -> None:
        api = self._api(engine)
        key = self.payload["issue_key"]
        issue = api.repo.get_issue(key)
        # ``todo`` is the normal case; ``blocked`` too lets a dependency-blind
        # persona work an issue that is not actually ready (blocked→in_progress is
        # an allowed transition). Anything else means it was taken/removed — skip.
        if issue is None or issue.status not in ("todo", "blocked"):
            return
        api.transition_issue(key, "in_progress", actor=self.owner_id)

    def _on_done(self, engine: "Engine") -> None:
        api = self._api(engine)
        key = self.payload["issue_key"]
        issue = api.repo.get_issue(key)
        if issue is None or issue.status != "in_progress":
            return
        if issue.remaining_minutes > 0:
            api.log_work(key, issue.remaining_minutes, actor=self.owner_id)
        # ``auto_close`` (default True) preserves the standard finish-to-done flow;
        # a ``when_asked`` persona parks the work in ``in_review`` until a standup
        # or a Slack mention closes it (see pm.npc.reactions).
        to_status: "IssueStatus" = "done" if self.payload.get("auto_close", True) else "in_review"
        api.transition_issue(key, to_status, actor=self.owner_id)


class MeetingEvent(Event):
    """A meeting spanning `duration` ticks. start → meeting begins; done → meeting ends
    and its transcript becomes available. payload: meeting_id, title, kind
    (standup|adhoc|1on1|team), attendees, location, transcript_id, transcript_body.
    """

    type = EventType.MEETING

    def _on_start(self, engine: "Engine") -> None:
        p = self.payload
        now = engine.clock.now()
        engine.store.add_meeting(
            Meeting(
                id=p["meeting_id"],
                title=p.get("title", p.get("kind", "meeting")),
                start_tick=self.start_tick,
                end_tick=self.start_tick + self.duration,
                location=p.get("location", ""),
                created_by=self.owner_id,
                attendees={pid: "accepted" for pid in p.get("attendees", [])},
            )
        )
        engine.store.log_event(
            now, actor=self.owner_id, kind="meeting.kind", payload={"kind": p.get("kind", "adhoc")}
        )
        # Bridge into the activity system: an ``in_meeting`` activity (priority 100,
        # no effects — this event stays the single writer) occupies the attendees so
        # the meeting preempts any activity work they are in.
        if self.duration > 0:
            people = sorted(set(p.get("attendees", [])) | {self.owner_id})
            engine.activities.request(
                "in_meeting", people, self.duration, now,
                params={"meeting_id": p["meeting_id"], "event_id": self.id},
            )

    def _on_done(self, engine: "Engine") -> None:
        p = self.payload
        now = engine.clock.now()
        # Every meeting leaves a transcript when it ends (empty body by default).
        engine.store.add_transcript(
            Transcript(
                id=p.get("transcript_id", f"tr-{p['meeting_id']}"),
                meeting_id=p["meeting_id"],
                body=p.get("transcript_body", ""),
                available_tick=now,
            )
        )
        # Tasks named in the meeting land in the informal `task` table — work can
        # exist with a DRI and status without any Jira ticket being filed.
        known = {t.id: t for t in engine.store.list_tasks()} if p.get("tasks") else {}
        for entry in p.get("tasks", []):
            existing = known.get(entry["id"])
            engine.store.upsert_task(
                Task(
                    id=entry["id"],
                    title=entry.get("title", existing.title if existing else entry["id"]),
                    dri_id=entry.get("dri_id", existing.dri_id if existing else None),
                    status=entry.get("status", existing.status if existing else "todo"),
                    source_meeting_id=existing.source_meeting_id if existing else p["meeting_id"],
                    created_tick=existing.created_tick if existing else now,
                    updated_tick=now,
                )
            )


EVENT_REGISTRY: dict[EventType, type[Event]] = {
    EventType.EMAIL_SEND: EmailSendEvent,
    EventType.EMAIL_READ: EmailReadEvent,
    EventType.SLACK_SEND: SlackSendEvent,
    EventType.SLACK_READ: SlackReadEvent,
    EventType.JIRA_TICKET: JiraTicketEvent,
    EventType.MEETING: MeetingEvent,
    EventType.OOO: OOOEvent,
}
