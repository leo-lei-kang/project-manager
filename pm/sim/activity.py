"""Activities — self-contained durative units of *doing* in the simulation.

An **Activity** is a durative thing an NPC (or several) does over sim-time. It is fully
self-contained:

  * a **kind** (meeting, jira_work, write_doc, review_doc, slack_send, coffee_break,
    watch_worldcup, …),
  * one or more **attenders** (`attendees`, at least one),
  * a **duration** (`duration_needed`) that burns down while running, and
  * a **state**: ``backlogged → started → (interrupted ↔ started) → done`` (+ ``cancelled``).

An activity does **not** reference a Task or Jira issue — it just runs and, on start/done,
applies a kind-specific side effect (post a message, write a doc, produce a transcript, …).
Kinds are a small **registry** of specs ``{name, priority, on_start, on_done}``; adding a
kind is one registration. A generic ``params`` dict carries whatever an effect needs.

``ActivityManager`` enforces that **each NPC is in at most one ``started`` activity at a
time**, across all attenders. Every activity is interruptible: a higher-priority activity
preempts the lower-priority ones occupying its attenders (they go ``interrupted`` and resume
later); if any attender is already in a ``>=``-priority activity, the incoming one waits
(``backlogged``). Priority is the only per-kind knob besides the two effect hooks.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, field_validator

from pm.world.models import Document, Meeting, Message, Transcript

if TYPE_CHECKING:
    from pm.sim.engine import Engine

ActivityState = Literal["backlogged", "started", "interrupted", "done", "cancelled"]


class Activity(BaseModel):
    id: int | None = None
    kind: str
    attendees: list[str]
    priority: int
    duration_needed: int
    remaining: int
    state: ActivityState = "backlogged"
    started_tick: int | None = None
    created_tick: int = 0
    params: dict = Field(default_factory=dict)

    @field_validator("attendees")
    @classmethod
    def _at_least_one_attender(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("an activity must have at least one attender")
        return v


# --------------------------------------------------------------------------- #
# Kind effects. Each takes (engine, activity); default is a no-op. All world
# mutation goes through engine.store (the single writer).
# --------------------------------------------------------------------------- #
def _noop(engine: "Engine", a: Activity) -> None:
    pass


def _meeting_start(engine: "Engine", a: Activity) -> None:
    p, now = a.params, engine.clock.now()
    engine.store.add_meeting(Meeting(
        id=p.get("meeting_id", f"mtg-{a.id}"),
        title=p.get("title", p.get("kind", "meeting")),
        start_tick=now, end_tick=now + a.duration_needed,
        location=p.get("location", ""), created_by=a.attendees[0],
        attendees={x: "accepted" for x in a.attendees},
    ))


def _meeting_done(engine: "Engine", a: Activity) -> None:
    p = a.params
    if "transcript_id" in p:
        engine.store.add_transcript(Transcript(
            id=p["transcript_id"], meeting_id=p.get("meeting_id", f"mtg-{a.id}"),
            body=p.get("transcript_body", ""), available_tick=engine.clock.now(),
        ))


def _write_doc_done(engine: "Engine", a: Activity) -> None:
    p, now = a.params, engine.clock.now()
    engine.store.add_document(Document(
        id=p["doc_id"], title=p.get("title", ""), body=p.get("body", ""),
        owner_id=a.attendees[0], created_tick=now, updated_tick=now,
    ))


def _review_doc_done(engine: "Engine", a: Activity) -> None:
    engine.store.log_event(engine.clock.now(), actor=a.attendees[0],
                           kind="doc.reviewed", payload={"doc_id": a.params.get("doc_id")})


def _slack_send_done(engine: "Engine", a: Activity) -> None:
    p = a.params
    engine.store.add_message(Message(
        id=p["message_id"], channel_id=p["channel_id"], sender_id=a.attendees[0],
        body=p.get("body", ""), sent_tick=engine.clock.now(),
    ))


def _jira_work_done(engine: "Engine", a: Activity) -> None:
    # Self-contained: records that work happened; it does NOT track a Jira issue.
    engine.store.log_event(engine.clock.now(), actor=a.attendees[0],
                           kind="ticket.worked", payload={"issue_key": a.params.get("issue_key")})


@dataclass(frozen=True)
class ActivityKind:
    name: str
    priority: int
    on_start: Callable[["Engine", Activity], None] = _noop
    on_done: Callable[["Engine", Activity], None] = _noop


ACTIVITY_KINDS: dict[str, ActivityKind] = {
    "meeting": ActivityKind("meeting", 100, on_start=_meeting_start, on_done=_meeting_done),
    "jira_work": ActivityKind("jira_work", 40, on_done=_jira_work_done),
    "write_doc": ActivityKind("write_doc", 35, on_done=_write_doc_done),
    "review_doc": ActivityKind("review_doc", 30, on_done=_review_doc_done),
    "slack_send": ActivityKind("slack_send", 20, on_done=_slack_send_done),
    "coffee_break": ActivityKind("coffee_break", 5),
    "watch_worldcup": ActivityKind("watch_worldcup", 5),
}

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS activity (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        kind            TEXT    NOT NULL,
        attendees_json  TEXT    NOT NULL DEFAULT '[]',
        priority        INTEGER NOT NULL,
        duration_needed INTEGER NOT NULL,
        remaining       INTEGER NOT NULL,
        state           TEXT    NOT NULL DEFAULT 'backlogged'
                        CHECK (state IN ('backlogged','started','interrupted','done','cancelled')),
        started_tick    INTEGER,
        created_tick    INTEGER NOT NULL DEFAULT 0,
        params_json     TEXT    NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_activity_state ON activity (state)",
)


class ActivityManager:
    """Per-NPC scheduler: one started activity per attender, priority interrupt/resume."""

    def __init__(
        self, engine: "Engine",
        idle_filler: Callable[["ActivityManager", int], None] | None = None,
    ) -> None:
        self._engine = engine
        self._store = engine.store
        self._idle_filler = idle_filler
        for stmt in _DDL:
            self._store.db.execute(stmt)

    # -- persistence ---------------------------------------------------------
    def _insert(self, a: Activity) -> int:
        cur = self._store.db.execute(
            "INSERT INTO activity (kind, attendees_json, priority, duration_needed, "
            "remaining, state, started_tick, created_tick, params_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (a.kind, json.dumps(a.attendees), a.priority, a.duration_needed, a.remaining,
             a.state, a.started_tick, a.created_tick, json.dumps(a.params)),
        )
        a.id = int(cur.lastrowid)
        return a.id

    def _update(self, a: Activity) -> None:
        self._store.db.execute(
            "UPDATE activity SET remaining = ?, state = ?, started_tick = ? WHERE id = ?",
            (a.remaining, a.state, a.started_tick, a.id),
        )

    @staticmethod
    def _row(r) -> Activity:
        return Activity(
            id=r["id"], kind=r["kind"], attendees=json.loads(r["attendees_json"]),
            priority=r["priority"], duration_needed=r["duration_needed"],
            remaining=r["remaining"], state=r["state"], started_tick=r["started_tick"],
            created_tick=r["created_tick"], params=json.loads(r["params_json"]),
        )

    def get(self, activity_id: int) -> Activity | None:
        r = self._store.db.query_one("SELECT * FROM activity WHERE id = ?", (activity_id,))
        return self._row(r) if r else None

    def _in_states(self, *states: str) -> list[Activity]:
        placeholders = ",".join("?" for _ in states)
        rows = self._store.db.query_all(
            f"SELECT * FROM activity WHERE state IN ({placeholders}) ORDER BY id", states)
        return [self._row(r) for r in rows]

    # -- queries -------------------------------------------------------------
    def started_for(self, npc: str) -> Activity | None:
        for a in self._in_states("started"):
            if npc in a.attendees:
                return a
        return None

    def is_idle(self, npc: str) -> bool:
        if self.started_for(npc) is not None:
            return False
        return not any(npc in a.attendees for a in self._in_states("backlogged", "interrupted"))

    # -- public API ----------------------------------------------------------
    def request(
        self, kind: str, attendees: list[str], duration_needed: int, now: int,
        *, params: dict | None = None,
    ) -> Activity:
        """Enqueue a new activity and dispatch (may start, interrupt others, or wait)."""
        if kind not in ACTIVITY_KINDS:
            raise ValueError(f"unknown activity kind {kind!r}")
        a = Activity(
            kind=kind, attendees=list(attendees), priority=ACTIVITY_KINDS[kind].priority,
            duration_needed=duration_needed, remaining=duration_needed,
            state="backlogged", created_tick=now, params=params or {},
        )
        self._insert(a)
        self._dispatch(now)
        return self.get(a.id)

    def tick(self, now: int) -> None:
        """Advance one minute: burn down started activities, complete, dispatch."""
        for a in self._in_states("started"):
            a.remaining -= 1
            if a.remaining <= 0:
                self._complete(a)
            else:
                self._update(a)
        self._dispatch(now)
        if self._idle_filler is not None:
            self._idle_filler(self, now)

    # -- internals -----------------------------------------------------------
    def _dispatch(self, now: int) -> None:
        # Highest priority first so a stronger activity claims its attenders first.
        for a in sorted(self._in_states("backlogged", "interrupted"),
                        key=lambda a: (-a.priority, a.id)):
            currents = {}
            for p in a.attendees:
                c = self.started_for(p)
                if c is not None:
                    currents[c.id] = c
            if any(c.priority >= a.priority for c in currents.values()):
                continue  # an attender is in an equal-or-higher activity: wait
            for c in currents.values():
                self._interrupt(c)  # all activities are interruptible
            self._start(a, now)

    def _start(self, a: Activity, now: int) -> None:
        a.state = "started"
        a.started_tick = now
        self._update(a)
        ACTIVITY_KINDS[a.kind].on_start(self._engine, a)

    def _interrupt(self, a: Activity) -> None:
        a.state = "interrupted"  # remaining frozen — resumes with work left
        self._update(a)

    def _complete(self, a: Activity) -> None:
        a.state = "done"
        a.remaining = 0
        self._update(a)
        ACTIVITY_KINDS[a.kind].on_done(self._engine, a)
