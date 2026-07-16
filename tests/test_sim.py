"""Durative events + engine: lifecycle, per-minute stepping, task phases, conditionals."""

from __future__ import annotations

import pytest

from pm.db.store import Store
from pm.sim.clock import SimClock
from pm.sim.engine import Engine
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.sim.events import (
    EventStatus,
    MeetingEvent,
    SlackSendEvent,
    JiraTicketEvent,
)
from pm.sim.scheduler import Scheduler
from pm.world.models import Person, Project


@pytest.fixture
def store(tmp_path) -> Store:
    s = Store.open(str(tmp_path / "world.db"), create=True)
    yield s
    s.close()


@pytest.fixture
def engine(store: Store) -> Engine:
    return Engine(store)


def _seed_people(store: Store) -> None:
    store.add_person(Person(id="u1", name="Priya", role="Engineer"))
    store.add_person(Person(id="mgr", name="Sam", role="Manager"))


def _seed_channel(store: Store, cid: str = "c1") -> None:
    store.db.execute(
        "INSERT INTO channel (id, name, kind) VALUES (?, ?, 'channel')", (cid, cid)
    )


def _log_kinds(store: Store) -> list[str]:
    return [e.kind for e in store.read_log()]


# -- scheduler ---------------------------------------------------------------


def test_scheduler_rejects_past(store: Store) -> None:
    clock = SimClock(store)
    clock.advance(50)
    sched = Scheduler(store, clock)
    with pytest.raises(ValueError):
        sched.schedule(SlackSendEvent(owner_id="u1", start_tick=10, payload={}))


def test_seq_is_monotonic_across_reopen(tmp_path) -> None:
    path = str(tmp_path / "world.db")
    store = Store.open(path, create=True)
    Scheduler(store, SimClock(store)).schedule(
        SlackSendEvent(owner_id="u1", start_tick=1, payload={})
    )
    store.close()

    store2 = Store.open(path)
    sched = Scheduler(store2, SimClock(store2))
    assert sched._next_seq == 1  # must not reuse seq 0
    store2.close()


# -- event lifecycle ---------------------------------------------------------


def test_instantaneous_event_starts_and_finishes_same_tick(engine: Engine) -> None:
    _seed_people(engine.store)
    _seed_channel(engine.store)

    engine.schedule(
        SlackSendEvent(
            owner_id="u1", start_tick=2,
            payload={"message_id": "m1", "channel_id": "c1", "body": "hi"},
        )
    )
    engine.advance(2)

    assert engine.store.list_messages("c1")[0].body == "hi"  # delivered same tick
    kinds = _log_kinds(engine.store)
    assert "slack.send.start" in kinds
    assert "slack.send.done" in kinds


def test_durative_meeting_counts_down_then_completes(engine: Engine) -> None:
    _seed_people(engine.store)
    engine.schedule(
        MeetingEvent(
            owner_id="u1",
            start_tick=1,
            duration=30,
            payload={
                "meeting_id": "m1",
                "kind": "standup",
                "attendees": ["u1"],
                "transcript_id": "tr1",
                "transcript_body": "Priya: pipeline coming along.",
            },
        )
    )

    engine.advance(1)  # meeting begins at tick 1
    active = engine.store.active_events()
    assert len(active) == 1
    assert active[0].remaining(engine.clock.now()) == 30  # 30 ticks left

    engine.advance_to(31)  # start_tick(1) + duration(30)
    assert engine.store.count_active_events() == 0
    tr = engine.store.db.query_one("SELECT * FROM transcript WHERE id = 'tr1'")
    assert tr is not None and tr["available_tick"] == 31


def test_jira_ticket_phases_and_unblock(engine: Engine) -> None:
    _seed_people(engine.store)
    engine.store.add_project(Project(id="p1", name="Billing"))
    repo = JiraRepository(engine.store)
    repo.ensure_schema()
    api = JiraApi(repo, engine)
    t1 = api.create_issue("p1", "task", "Choose STT", estimate_minutes=5, assignee="u1")
    t2 = api.create_issue(
        "p1", "task", "Wire STT", estimate_minutes=5, assignee="u1", depends_on=[t1.id]
    )
    assert api.get_issue(t2.id).status == "blocked"  # gated on t1

    engine.schedule(
        JiraTicketEvent(owner_id="u1", start_tick=1, duration=5, payload={"issue_key": t1.id})
    )

    engine.advance(1)  # work begins
    assert api.get_issue(t1.id).status == "in_progress"

    engine.advance_to(6)  # start_tick(1) + duration(5)
    assert api.get_issue(t1.id).status == "done"
    assert api.get_issue(t2.id).status == "todo"  # dependent unblocked

    kinds = _log_kinds(engine.store)
    assert "jira_ticket.start" in kinds
    assert "jira_ticket.done" in kinds


# -- sync/async boundary -----------------------------------------------------


def test_perform_action_applies_effect_then_advances(engine: Engine) -> None:
    _seed_people(engine.store)
    _seed_channel(engine.store)
    order: list[str] = []

    def effect() -> None:
        order.append(f"effect@{engine.clock.now()}")  # runs at current tick

    engine.schedule(
        SlackSendEvent(
            owner_id="u1",
            start_tick=4,
            payload={"message_id": "m1", "channel_id": "c1", "body": "hi"},
        )
    )
    fired = engine.perform_action(actor="agent", cost=6, effect=effect)

    assert order == ["effect@0"]
    assert engine.clock.now() == 6
    assert engine.store.list_messages("c1")[0].body == "hi"  # delivered during advance
    assert any(e.type.value == "slack.send" for e in fired)


def test_advance_to_rejects_past(engine: Engine) -> None:
    engine.advance(10)
    with pytest.raises(ValueError):
        engine.advance_to(5)


def test_event_status_flips_pending_active_done(engine: Engine) -> None:
    _seed_people(engine.store)
    engine.schedule(
        MeetingEvent(
            owner_id="u1", start_tick=1, duration=3,
            payload={"meeting_id": "m1", "kind": "standup", "attendees": ["u1"]},
        )
    )
    assert engine.scheduler.pending_count() == 1

    engine.advance(1)
    assert engine.scheduler.active_count() == 1  # now running

    engine.advance_to(4)
    assert engine.scheduler.pending_count() == 0
    assert engine.scheduler.active_count() == 0  # completed
    row = engine.store.db.query_one("SELECT status FROM event LIMIT 1")
    assert row["status"] == EventStatus.DONE.value