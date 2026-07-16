"""Typed repository over :class:`~pm.db.database.Database`.

``Store`` is the single interface the rest of the system (engine, tools, NPCs,
evaluator) uses to read and mutate the world. No raw SQL leaks past this module.
It maps ``sqlite3.Row`` objects to the Pydantic models in :mod:`pm.world.models`
and back.

Only the engine should call the mutating methods during a run — that is how we
keep "one owner mutates world state" true in practice.
"""

from __future__ import annotations

import json
from datetime import datetime

from collections.abc import Callable
from typing import TYPE_CHECKING

from pm.db.database import Database
from pm.world.models import (
    Document,
    Email,
    LogEntry,
    Meeting,
    Message,
    Person,
    Project,
    Task,
    Transcript,
)

if TYPE_CHECKING:
    from pm.sim.events import Event

# Entity tables reported by `count_entities` (excludes sim machinery).
_ENTITY_TABLES = (
    "person",
    "project",
    "channel",
    "message",
    "email",
    "meeting",
    "transcript",
    "task",
    "document",
)


class Store:
    def __init__(self, db: Database) -> None:
        self.db = db
        # Optional observer invoked with each LogEntry as it is appended —
        # the live-streaming hook `pm sim` uses to narrate the run.
        self.on_log: Callable[[LogEntry], None] | None = None

    @classmethod
    def open(cls, path: str, *, create: bool = False) -> "Store":
        """Open a store; when ``create`` is set, apply the schema first."""
        db = Database.connect(path)
        if create:
            db.apply_schema()
        return cls(db)

    def close(self) -> None:
        self.db.close()

    # -- meta / clock --------------------------------------------------------

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
        )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.db.query_one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else default

    def get_tick(self) -> int:
        return int(self.get_meta("current_tick", "0"))

    def set_tick(self, tick: int) -> None:
        self.set_meta("current_tick", str(tick))

    # -- observability trace -------------------------------------------------

    def log_event(
        self, sim_tick: int, actor: str, kind: str, payload: dict | None = None
    ) -> None:
        """Append an immutable record to the observability trace."""
        # %f is microseconds; keep the first three digits for milliseconds.
        wall_time = datetime.now().strftime("%y/%m/%d %H:%M:%S.%f")[:-3]
        self.db.execute(
            "INSERT INTO event_log (sim_tick, actor, kind, payload_json, wall_time) "
            "VALUES (?, ?, ?, ?, ?)",
            (sim_tick, actor, kind, json.dumps(payload or {}), wall_time),
        )
        if self.on_log is not None:
            self.on_log(LogEntry(sim_tick=sim_tick, actor=actor, kind=kind,
                                 payload=payload or {}, wall_time=wall_time))

    def read_log(self, limit: int | None = None) -> list[LogEntry]:
        sql = "SELECT * FROM event_log ORDER BY id"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [
            LogEntry(
                id=r["id"],
                sim_tick=r["sim_tick"],
                actor=r["actor"],
                kind=r["kind"],
                payload=json.loads(r["payload_json"]),
                # Tolerate rows from databases created before the column existed.
                wall_time=r["wall_time"] if "wall_time" in r.keys() else "",
            )
            for r in self.db.query_all(sql)
        ]

    # -- durative event queue ------------------------------------------------

    def upsert_event(self, event: "Event") -> int:
        """Insert a new event (assigning its id) or update a mutating one in place.

        A single row per event: its ``status`` flips pending -> active -> done, so
        insert happens once at schedule time and updates carry the lifecycle.
        """
        if event.id is None:
            cur = self.db.execute(
                "INSERT INTO event (type, owner_id, initiator_id, start_tick, duration, seq, "
                "status, payload_json, created_tick, done_tick) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.type.value,
                    event.owner_id,
                    event.initiator_id,
                    event.start_tick,
                    event.duration,
                    event.seq,
                    event.status.value,
                    json.dumps(event.payload),
                    event.created_tick,
                    event.done_tick,
                ),
            )
            event.id = int(cur.lastrowid)
        else:
            self.db.execute(
                "UPDATE event SET status = ?, payload_json = ?, done_tick = ? WHERE id = ?",
                (
                    event.status.value,
                    json.dumps(event.payload),
                    event.done_tick,
                    event.id,
                ),
            )
        return event.id

    def pending_events_starting_at(self, tick: int) -> list["Event"]:
        """Pending events whose ``start_tick`` has arrived, in deterministic order."""
        from pm.sim.events import Event

        rows = self.db.query_all(
            "SELECT * FROM event WHERE status = 'pending' AND start_tick <= ? "
            "ORDER BY start_tick, seq, id",
            (tick,),
        )
        return [Event.from_row(r) for r in rows]

    def active_events(self) -> list["Event"]:
        """Currently-running events, in deterministic order."""
        from pm.sim.events import Event

        rows = self.db.query_all(
            "SELECT * FROM event WHERE status = 'active' ORDER BY start_tick, seq, id"
        )
        return [Event.from_row(r) for r in rows]

    def events_done_at(self, tick: int) -> list["Event"]:
        """Events that completed at ``tick``, in deterministic order (for reactions)."""
        from pm.sim.events import Event

        rows = self.db.query_all(
            "SELECT * FROM event WHERE status = 'done' AND done_tick = ? "
            "ORDER BY start_tick, seq, id",
            (tick,),
        )
        return [Event.from_row(r) for r in rows]

    def next_event_start_tick(self) -> int | None:
        """Earliest ``start_tick`` among pending events, or ``None`` if none pending."""
        row = self.db.query_one(
            "SELECT MIN(start_tick) AS t FROM event WHERE status = 'pending'"
        )
        return row["t"] if row and row["t"] is not None else None

    def count_pending_events(self) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM event WHERE status = 'pending'"
        )
        return row["n"] if row else 0

    def count_active_events(self) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM event WHERE status = 'active'"
        )
        return row["n"] if row else 0

    def max_event_seq(self) -> int:
        """Largest ``seq`` assigned so far, or -1 if none.

        Lets the engine resume its monotonic sequence counter without reusing
        values after a save/reload.
        """
        row = self.db.query_one("SELECT MAX(seq) AS s FROM event")
        return row["s"] if row and row["s"] is not None else -1

    def get_event(self, event_id: int) -> "Event | None":
        from pm.sim.events import Event

        r = self.db.query_one("SELECT * FROM event WHERE id = ?", (event_id,))
        return Event.from_row(r) if r else None

    def reschedule_event(self, event: "Event") -> None:
        """Update a queued event's timing/status in place (used by calendar resolution).

        Unlike :meth:`upsert_event`'s update path (status/payload/done only), this also
        rewrites ``start_tick`` and ``duration`` — needed when an event is deferred
        (start moves) or paused/resumed (duration extends).
        """
        self.db.execute(
            "UPDATE event SET start_tick = ?, duration = ?, status = ?, "
            "payload_json = ?, done_tick = ? WHERE id = ?",
            (
                event.start_tick,
                event.duration,
                event.status.value,
                json.dumps(event.payload),
                event.done_tick,
                event.id,
            ),
        )

    # -- occupancy calendar --------------------------------------------------

    def add_occupancy(self, person_id: str, event: "Event", priority: int) -> None:
        """Reserve one calendar block for ``person_id`` covering ``event``'s window."""
        self.db.execute(
            "INSERT INTO occupancy (person_id, event_id, kind, priority, start_tick, end_tick) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                person_id,
                event.id,
                event.type.value,
                priority,
                event.start_tick,
                event.start_tick + event.duration,
            ),
        )

    def occupancy_overlaps(self, person_ids, start: int, end: int) -> list[dict]:
        """Blocks overlapping ``[start, end)`` for any of ``person_ids`` (half-open)."""
        ids = list(person_ids)
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self.db.query_all(
            f"SELECT * FROM occupancy WHERE person_id IN ({placeholders}) "
            "AND start_tick < ? AND end_tick > ? ORDER BY start_tick, id",
            (*ids, end, start),
        )
        return [dict(r) for r in rows]

    def clear_occupancy(self, event_id: int) -> None:
        """Remove all calendar blocks for an event (before re-laying them)."""
        self.db.execute("DELETE FROM occupancy WHERE event_id = ?", (event_id,))

    # -- people --------------------------------------------------------------

    def add_person(self, person: Person) -> None:
        self.db.execute(
            "INSERT INTO person (id, name, role, is_agent, persona_json, delay_min, delay_max) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                person.id,
                person.name,
                person.role,
                int(person.is_agent),
                json.dumps(person.persona),
                person.delay_min,
                person.delay_max,
            ),
        )

    def get_person(self, person_id: str) -> Person | None:
        r = self.db.query_one("SELECT * FROM person WHERE id = ?", (person_id,))
        return self._row_to_person(r) if r else None

    def list_people(self) -> list[Person]:
        return [
            self._row_to_person(r)
            for r in self.db.query_all("SELECT * FROM person ORDER BY id")
        ]

    @staticmethod
    def _row_to_person(r) -> Person:
        return Person(
            id=r["id"],
            name=r["name"],
            role=r["role"],
            is_agent=bool(r["is_agent"]),
            persona=json.loads(r["persona_json"]),
            delay_min=r["delay_min"],
            delay_max=r["delay_max"],
        )

    # -- projects ------------------------------------------------------------

    def add_project(self, project: Project) -> None:
        self.db.execute(
            "INSERT INTO project (id, name, description, status, deadline_tick) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                project.id,
                project.name,
                project.description,
                project.status,
                project.deadline_tick,
            ),
        )

    def get_project(self, project_id: str) -> Project | None:
        r = self.db.query_one("SELECT * FROM project WHERE id = ?", (project_id,))
        if not r:
            return None
        return Project(
            id=r["id"],
            name=r["name"],
            description=r["description"],
            status=r["status"],
            deadline_tick=r["deadline_tick"],
        )

    # -- chat ----------------------------------------------------------------

    def add_message(self, message: Message) -> None:
        self.db.execute(
            "INSERT INTO message (id, channel_id, sender_id, body, sent_tick) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                message.id,
                message.channel_id,
                message.sender_id,
                message.body,
                message.sent_tick,
            ),
        )

    def list_messages(self, channel_id: str) -> list[Message]:
        rows = self.db.query_all(
            "SELECT * FROM message WHERE channel_id = ? ORDER BY sent_tick, id",
            (channel_id,),
        )
        return [
            Message(
                id=r["id"],
                channel_id=r["channel_id"],
                sender_id=r["sender_id"],
                body=r["body"],
                sent_tick=r["sent_tick"],
            )
            for r in rows
        ]

    def count_messages(self, channel_id: str) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM message WHERE channel_id = ?", (channel_id,)
        )
        return row["n"] if row else 0

    # -- email ---------------------------------------------------------------

    def add_email(self, email: Email) -> None:
        """Deliver an email: ensure its thread exists, insert it, wire recipients."""
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO email_thread (id, subject) VALUES (?, ?)",
                (email.thread_id, email.subject),
            )
            conn.execute(
                "INSERT INTO email (id, thread_id, sender_id, subject, body, sent_tick) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    email.id,
                    email.thread_id,
                    email.sender_id,
                    email.subject,
                    email.body,
                    email.sent_tick,
                ),
            )
            for pid in email.to:
                conn.execute(
                    "INSERT OR IGNORE INTO email_recipient (email_id, person_id, kind) "
                    "VALUES (?, ?, 'to')",
                    (email.id, pid),
                )
            for pid in email.cc:
                conn.execute(
                    "INSERT OR IGNORE INTO email_recipient (email_id, person_id, kind) "
                    "VALUES (?, ?, 'cc')",
                    (email.id, pid),
                )

    # -- calendar ------------------------------------------------------------

    def add_meeting(self, meeting: Meeting) -> None:
        """Create a meeting (idempotent) and its attendee rows."""
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO meeting (id, title, start_tick, end_tick, location, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    meeting.id,
                    meeting.title,
                    meeting.start_tick,
                    meeting.end_tick,
                    meeting.location,
                    meeting.created_by,
                ),
            )
            for pid, response in meeting.attendees.items():
                conn.execute(
                    "INSERT OR IGNORE INTO meeting_attendee (meeting_id, person_id, response) "
                    "VALUES (?, ?, ?)",
                    (meeting.id, pid, response),
                )

    def list_meetings(self, attendee_id: str | None = None) -> list[Meeting]:
        """Meetings in start order; when ``attendee_id`` is given, only theirs."""
        if attendee_id is None:
            rows = self.db.query_all("SELECT * FROM meeting ORDER BY start_tick, id")
        else:
            rows = self.db.query_all(
                "SELECT m.* FROM meeting m "
                "JOIN meeting_attendee a ON a.meeting_id = m.id "
                "WHERE a.person_id = ? ORDER BY m.start_tick, m.id",
                (attendee_id,),
            )
        return [self._row_to_meeting(r) for r in rows]

    def _row_to_meeting(self, r) -> Meeting:
        attendees = {
            row["person_id"]: row["response"]
            for row in self.db.query_all(
                "SELECT person_id, response FROM meeting_attendee WHERE meeting_id = ? "
                "ORDER BY person_id",
                (r["id"],),
            )
        }
        return Meeting(
            id=r["id"],
            title=r["title"],
            start_tick=r["start_tick"],
            end_tick=r["end_tick"],
            location=r["location"],
            created_by=r["created_by"],
            attendees=attendees,
        )

    def add_transcript(self, transcript: Transcript) -> None:
        self.db.execute(
            "INSERT INTO transcript (id, meeting_id, body, available_tick) "
            "VALUES (?, ?, ?, ?)",
            (
                transcript.id,
                transcript.meeting_id,
                transcript.body,
                transcript.available_tick,
            ),
        )

    def list_transcripts(self, available_by: int) -> list[Transcript]:
        """Transcripts available at or before ``available_by`` (a meeting's end tick)."""
        rows = self.db.query_all(
            "SELECT * FROM transcript WHERE available_tick <= ? "
            "ORDER BY available_tick, id",
            (available_by,),
        )
        return [
            Transcript(
                id=r["id"],
                meeting_id=r["meeting_id"],
                body=r["body"],
                available_tick=r["available_tick"],
            )
            for r in rows
        ]

    # -- docs ----------------------------------------------------------------

    # -- informal tasks (meeting-notes mirror of Jira tickets) ----------------

    def upsert_task(self, task: Task) -> None:
        """Create or update an informal task (written when a meeting ends)."""
        self.db.execute(
            "INSERT OR REPLACE INTO task (id, title, dri_id, status, "
            "source_meeting_id, created_tick, updated_tick) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                task.id,
                task.title,
                task.dri_id,
                task.status,
                task.source_meeting_id,
                task.created_tick,
                task.updated_tick,
            ),
        )

    def list_tasks(self) -> list[Task]:
        """All informal tasks, in id order."""
        rows = self.db.query_all("SELECT * FROM task ORDER BY id")
        return [
            Task(
                id=r["id"],
                title=r["title"],
                dri_id=r["dri_id"],
                status=r["status"],
                source_meeting_id=r["source_meeting_id"],
                created_tick=r["created_tick"],
                updated_tick=r["updated_tick"],
            )
            for r in rows
        ]

    def add_document(self, document: Document) -> None:
        """Create or update a document (used by the write_doc activity)."""
        self.db.execute(
            "INSERT OR REPLACE INTO document (id, title, body, owner_id, created_tick, updated_tick) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                document.id,
                document.title,
                document.body,
                document.owner_id,
                document.created_tick,
                document.updated_tick,
            ),
        )

    # -- snapshots / reporting ----------------------------------------------

    def count_entities(self) -> dict[str, int]:
        """Return per-table row counts for the world entities."""
        counts: dict[str, int] = {}
        for table in _ENTITY_TABLES:
            row = self.db.query_one(f"SELECT COUNT(*) AS n FROM {table}")
            counts[table] = row["n"] if row else 0
        return counts

    def export_state(self) -> dict:
        """A JSON-serializable snapshot of the world, for eval and inspection."""
        return {
            "tick": self.get_tick(),
            "seed": self.get_meta("seed"),
            "scenario": self.get_meta("scenario"),
            "people": [p.model_dump() for p in self.list_people()],
            "counts": self.count_entities(),
        }
