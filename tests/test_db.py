"""Foundation tests: schema, round-tripping, event log, event queue, clock."""

from __future__ import annotations

import pytest

from pm.db.database import Database
from pm.db.store import Store
from pm.sim.clock import SimClock
from pm.sim.events import EventStatus, SlackSendEvent
from pm.world.models import Message, Person


@pytest.fixture
def store(tmp_path) -> Store:
    s = Store.open(str(tmp_path / "world.db"), create=True)
    yield s
    s.close()


def test_schema_applies_idempotently(tmp_path) -> None:
    path = str(tmp_path / "world.db")
    db = Database.connect(path)
    db.apply_schema()
    db.apply_schema()  # second application must be a no-op, not an error
    tables = {
        r["name"]
        for r in db.query_all("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for expected in (
        "meta",
        "person",
        "event",
        "event_log",
    ):
        assert expected in tables
    assert (
        db.query_one("SELECT value FROM meta WHERE key='schema_version'")["value"]
        == "2"
    )
    db.close()


def test_foreign_keys_enforced(store: Store) -> None:
    # A message referencing a non-existent channel must be rejected.
    with pytest.raises(Exception):
        store.add_message(
            Message(id="m1", channel_id="missing", sender_id="u1", body="hi", sent_tick=1)
        )


def test_person_persona_json_round_trip(store: Store) -> None:
    store.add_person(
        Person(
            id="u2", name="Sam", role="PM", is_agent=True, persona={"goals": ["ship"]}
        )
    )
    p = store.get_person("u2")
    assert p.is_agent is True
    assert p.persona == {"goals": ["ship"]}


def test_message_round_trip(store: Store) -> None:
    store.add_person(Person(id="u1", name="Dana"))
    store.db.execute(
        "INSERT INTO channel (id, name, kind) VALUES ('c1', 'general', 'channel')"
    )
    store.add_message(
        Message(id="m1", channel_id="c1", sender_id="u1", body="hi", sent_tick=3)
    )
    msgs = store.list_messages("c1")
    assert len(msgs) == 1 and msgs[0].body == "hi"


def test_event_log_appends_in_order(store: Store) -> None:
    store.log_event(1, actor="agent", kind="tool.chat", payload={"n": 1})
    store.log_event(2, actor="npc:dana", kind="chat.reply", payload={"n": 2})
    entries = store.read_log()
    assert [e.kind for e in entries] == ["tool.chat", "chat.reply"]
    assert entries[0].payload == {"n": 1}
    assert entries[1].sim_tick == 2


def test_event_queue_persists_orders_and_rehydrates(store: Store) -> None:
    # Two events at different start ticks; one earlier by (start_tick, seq).
    store.upsert_event(
        SlackSendEvent(owner_id="u1", start_tick=10, seq=1, payload={"message_id": "t"})
    )
    store.upsert_event(
        SlackSendEvent(owner_id="u1", start_tick=5, seq=0, payload={"message_id": "t"})
    )
    store.upsert_event(
        SlackSendEvent(owner_id="u1", start_tick=20, seq=0, payload={"message_id": "t"})
    )

    due = store.pending_events_starting_at(10)
    # Only events at/before tick 10, ordered by (start_tick, seq); rehydrated as the
    # correct subclass.
    assert [e.start_tick for e in due] == [5, 10]
    assert all(isinstance(e, SlackSendEvent) for e in due)
    assert store.max_event_seq() == 1

    # Flipping status to done removes it from the pending set.
    ev = due[0]
    ev.status = EventStatus.DONE
    store.upsert_event(ev)
    remaining = [e.start_tick for e in store.pending_events_starting_at(10)]
    assert remaining == [10]


def test_clock_advances_and_persists(store: Store) -> None:
    clock = SimClock(store)
    assert clock.now() == 0
    assert clock.advance(90) == 90
    assert store.get_tick() == 90
    assert clock.as_clock_str() == "Mon 10:30"  # 09:00 + 90 work-minutes
    with pytest.raises(ValueError):
        clock.advance(-1)
