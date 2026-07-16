"""Pydantic v2 models mirroring the world schema.

These are the typed representation used at the boundary between application code
and the database. ``Store`` maps ``sqlite3.Row`` objects to these models and
back. They also serialize cleanly to JSON for snapshots and inspection.

Time is always an integer sim tick. Enums are plain ``str``-typed literals so
they round-trip through SQLite (which has no native enum) without adapters.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ChannelKind = Literal["dm", "channel"]
RecipientKind = Literal["to", "cc"]
AttendeeResponse = Literal["accepted", "declined", "pending"]


class Person(BaseModel):
    id: str
    name: str
    role: str = ""
    is_agent: bool = False
    persona: dict = Field(default_factory=dict)
    delay_min: int = 0
    delay_max: int = 0


class Project(BaseModel):
    id: str
    name: str
    description: str = ""
    status: str = "active"
    deadline_tick: int | None = None


class Channel(BaseModel):
    id: str
    name: str
    kind: ChannelKind = "channel"
    members: list[str] = Field(default_factory=list)


class Message(BaseModel):
    id: str
    channel_id: str
    sender_id: str
    body: str
    sent_tick: int


class Email(BaseModel):
    id: str
    thread_id: str
    sender_id: str
    subject: str
    body: str
    sent_tick: int
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)


class Meeting(BaseModel):
    id: str
    title: str
    start_tick: int
    end_tick: int
    location: str = ""
    created_by: str | None = None
    attendees: dict[str, AttendeeResponse] = Field(default_factory=dict)


class Transcript(BaseModel):
    id: str
    meeting_id: str
    body: str
    available_tick: int


TaskStatus = Literal["todo", "in_progress", "done"]


class Task(BaseModel):
    """An informal work item — a mirror of a Jira ticket that lives in meeting notes.

    Created/updated when a meeting ends (from the meeting's payload), so work can
    carry a DRI and status without any Jira issue existing for it.
    """

    id: str
    title: str
    dri_id: str | None = None  # the directly responsible individual
    status: TaskStatus = "todo"
    source_meeting_id: str | None = None
    created_tick: int = 0
    updated_tick: int = 0


class Document(BaseModel):
    id: str
    title: str
    body: str = ""
    owner_id: str | None = None
    created_tick: int = 0
    updated_tick: int = 0


class LogEntry(BaseModel):
    """An append-only observability record from ``event_log``."""

    id: int | None = None
    sim_tick: int
    actor: str
    kind: str
    payload: dict = Field(default_factory=dict)
    wall_time: str = ""  # real time the row was recorded, "yy/mm/dd hh:mm:ss.mmm"
