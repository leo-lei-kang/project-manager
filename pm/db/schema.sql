-- Project Manager Simulation — world schema.
--
-- Design notes:
--   * SQLite is the SINGLE SOURCE OF TRUTH for the world. Tools/NPCs/eval are
--     views and commands over these tables; there is no parallel in-memory copy.
--   * All time columns are integer SIM TICKS (never wall-clock). One tick == one
--     simulated minute by convention; a 5-day work week is bounded by scenario data.
--   * `*_json` columns hold Pydantic-serialized blobs for open-ended structured
--     data (personas, event payloads) that we never need to query relationally.
--   * The engine is the only writer. FKs are ON to keep the graph consistent.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Meta / sim machinery
-- ---------------------------------------------------------------------------

-- Key/value config + clock. Holds: schema_version, scenario, seed, current_tick.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Durative event queue. Every activity is ONE row with a lifecycle status
-- (pending -> active -> done); completion is a status flip on this same row, not
-- a separate record. `(start_tick, seq)` is the deterministic ordering key.
-- Persisting the queue makes runs resumable and the async schedule inspectable.
CREATE TABLE IF NOT EXISTS event (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    type         TEXT    NOT NULL,
    owner_id     TEXT    NOT NULL DEFAULT '',   -- who carries out / is responsible for the event
    initiator_id TEXT,                          -- who triggered it (nullable; may differ from owner)
    start_tick   INTEGER NOT NULL,
    duration     INTEGER NOT NULL DEFAULT 0,   -- ticks; 0 == instantaneous
    seq          INTEGER NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'active', 'done', 'cancelled')),
    payload_json TEXT    NOT NULL DEFAULT '{}',
    created_tick INTEGER NOT NULL DEFAULT 0,
    done_tick    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_event_pending ON event (status, start_tick, seq);

-- Append-only observability trace of every action, tick, and state transition.
-- Never updated; feeds debugging and defensible grading (evidence per outcome).
CREATE TABLE IF NOT EXISTS event_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_tick     INTEGER NOT NULL,
    actor        TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    payload_json TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_event_log_tick ON event_log (sim_tick);

-- ---------------------------------------------------------------------------
-- World entities
-- ---------------------------------------------------------------------------

-- People: coworkers (NPCs) and the agent-under-test (is_agent = 1).
CREATE TABLE IF NOT EXISTS person (
    id           TEXT PRIMARY KEY,
    name         TEXT    NOT NULL,
    role         TEXT    NOT NULL DEFAULT '',
    is_agent     INTEGER NOT NULL DEFAULT 0,
    persona_json TEXT    NOT NULL DEFAULT '{}',
    delay_min    INTEGER NOT NULL DEFAULT 0,   -- min NPC response delay in ticks
    delay_max    INTEGER NOT NULL DEFAULT 0    -- max NPC response delay in ticks
);

-- Per-NPC time calendar. One block per (occupied person x durative event); the
-- planning/contention layer over the event queue. Higher `priority` wins: a
-- meeting bumps overlapping work later, work waits behind a meeting, two pieces of
-- work serialize. Instantaneous events reserve nothing. Half-open [start, end).
CREATE TABLE IF NOT EXISTS occupancy (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id  TEXT    NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    event_id   INTEGER NOT NULL REFERENCES event(id)  ON DELETE CASCADE,
    kind       TEXT    NOT NULL,
    priority   INTEGER NOT NULL,
    start_tick INTEGER NOT NULL,
    end_tick   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_occupancy_person ON occupancy (person_id, start_tick, end_tick);

CREATE TABLE IF NOT EXISTS project (
    id            TEXT PRIMARY KEY,
    name          TEXT    NOT NULL,
    description   TEXT    NOT NULL DEFAULT '',
    status        TEXT    NOT NULL DEFAULT 'active',
    deadline_tick INTEGER
);

-- Chat: channels (kind='channel') and direct messages (kind='dm').
CREATE TABLE IF NOT EXISTS channel (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'channel' CHECK (kind IN ('dm', 'channel'))
);

CREATE TABLE IF NOT EXISTS channel_member (
    channel_id TEXT NOT NULL REFERENCES channel(id) ON DELETE CASCADE,
    person_id  TEXT NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    PRIMARY KEY (channel_id, person_id)
);

CREATE TABLE IF NOT EXISTS message (
    id         TEXT PRIMARY KEY,
    channel_id TEXT    NOT NULL REFERENCES channel(id) ON DELETE CASCADE,
    sender_id  TEXT    NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    body       TEXT    NOT NULL,
    sent_tick  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_message_channel ON message (channel_id, sent_tick);

-- Email: threads with slower cadence than chat (enforced later by NPC delays).
CREATE TABLE IF NOT EXISTS email_thread (
    id      TEXT PRIMARY KEY,
    subject TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS email (
    id         TEXT PRIMARY KEY,
    thread_id  TEXT    NOT NULL REFERENCES email_thread(id) ON DELETE CASCADE,
    sender_id  TEXT    NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    subject    TEXT    NOT NULL,
    body       TEXT    NOT NULL,
    sent_tick  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_email_thread ON email (thread_id, sent_tick);

CREATE TABLE IF NOT EXISTS email_recipient (
    email_id  TEXT NOT NULL REFERENCES email(id) ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    kind      TEXT NOT NULL DEFAULT 'to' CHECK (kind IN ('to', 'cc')),
    PRIMARY KEY (email_id, person_id, kind)
);

-- Calendar / meetings.
CREATE TABLE IF NOT EXISTS meeting (
    id         TEXT PRIMARY KEY,
    title      TEXT    NOT NULL,
    start_tick INTEGER NOT NULL,
    end_tick   INTEGER NOT NULL,
    location   TEXT    NOT NULL DEFAULT '',
    created_by TEXT    REFERENCES person(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_meeting_start ON meeting (start_tick);

CREATE TABLE IF NOT EXISTS meeting_attendee (
    meeting_id TEXT NOT NULL REFERENCES meeting(id) ON DELETE CASCADE,
    person_id  TEXT NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    response   TEXT NOT NULL DEFAULT 'pending'
               CHECK (response IN ('accepted', 'declined', 'pending')),
    PRIMARY KEY (meeting_id, person_id)
);

-- Transcript rows exist only after a meeting fires; `available_tick` gates
-- attend-only discoverability of facts surfaced in the meeting.
CREATE TABLE IF NOT EXISTS transcript (
    id             TEXT PRIMARY KEY,
    meeting_id     TEXT    NOT NULL REFERENCES meeting(id) ON DELETE CASCADE,
    body           TEXT    NOT NULL,
    available_tick INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS document (
    id           TEXT PRIMARY KEY,
    title        TEXT    NOT NULL,
    body         TEXT    NOT NULL DEFAULT '',
    owner_id     TEXT    REFERENCES person(id) ON DELETE SET NULL,
    created_tick INTEGER NOT NULL DEFAULT 0,
    updated_tick INTEGER NOT NULL DEFAULT 0
);
