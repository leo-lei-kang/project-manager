"""Persona-driven NPC behavior: completion timing, selection order, dependency
awareness, and blocker-first prioritization.

Each test seeds a small cast with a specific :class:`~pm.npc.persona.Persona`,
drives a run (or a single hook tick), and asserts on the board state read back
through :class:`~pm.jira.api.JiraApi`. Determinism is exact: tick 0 is Monday
09:00 and the seeded RNG (meta ``seed`` == 42) reproduces the random pick.
"""

from __future__ import annotations

import random

import pytest

from pm.env import Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.sim.npc import WorkDriver
from pm.npc.cast import CastMember, seed_cast
from pm.npc.persona import (
    HEADS_DOWN,
    PERFECT,
    FREE_SPIRIT,
    Persona,
    from_person,
)
from pm.sim.events import MeetingEvent, SlackSendEvent
from pm.sim.simulation import Simulation
from pm.world.models import Project


@pytest.fixture
def env(tmp_path) -> Env:
    e = Env.make(run_id="persona", root=tmp_path)  # default seed 42
    e.store.add_project(Project(id="checkout", name="Checkout"))
    yield e
    e.close()


def _api(env: Env) -> JiraApi:
    repo = JiraRepository(env.store)
    repo.ensure_schema()
    return JiraApi(repo, env.engine)


def _member(pid: str, persona: Persona = PERFECT, discipline: str = "backend") -> CastMember:
    return CastMember(pid, pid.capitalize(), "Engineer", discipline, "member", True, persona=persona)


def _drive(env: Env, driver: WorkDriver, *, closes: bool = False) -> None:
    """Kickoff sweep + completion-driven week (optionally with the close reactions)."""
    env.engine.activities.on_activity_done = driver.on_activity_done
    if closes:
        env.engine.on_event_done = driver.on_event_done
    driver.sweep(env.engine)
    Simulation(env).run()


def test_persona_round_trips_through_seed_cast(env: Env) -> None:
    seed_cast(env.store, cast=[_member("alice", HEADS_DOWN), _member("dan", FREE_SPIRIT)])
    assert from_person(env.store.get_person("alice")) == HEADS_DOWN
    assert from_person(env.store.get_person("dan")) == FREE_SPIRIT
    # unknown / unseeded person falls back to the perfect worker
    assert from_person(env.store.get_person("nobody")) == PERFECT


def test_when_asked_holds_finished_work_in_review(env: Env) -> None:
    # Item 1: without a standup or a Slack ask, finished work is never marked done.
    seed_cast(env.store, cast=[_member("alice", HEADS_DOWN)])
    api = _api(env)
    task = api.create_issue("checkout", "task", "API", estimate_minutes=5,
                            assignee="alice", actor="alice")

    _drive(env, WorkDriver(api, ["alice"], "checkout"))

    assert api.get_issue(task.id).status == "in_review"  # done work, not closed


def test_standup_closes_in_review_work(env: Env) -> None:
    # Item 1: a completed standup prompts its attendees to close pending work.
    seed_cast(env.store, cast=[_member("alice", HEADS_DOWN)])
    api = _api(env)
    task = api.create_issue("checkout", "task", "API", estimate_minutes=5,
                            assignee="alice", actor="alice")
    env.engine.schedule(MeetingEvent(
        owner_id="alice", start_tick=20, duration=5,
        payload={"meeting_id": "m1", "kind": "standup", "attendees": ["alice"], "title": "Standup"},
    ))

    _drive(env, WorkDriver(api, ["alice"], "checkout"), closes=True)

    assert api.get_issue(task.id).status == "done"


def test_slack_mention_closes_in_review_work(env: Env) -> None:
    # Item 1: a Slack message naming the person closes their pending work once
    # they read it (within the hour); an unrelated message does not.
    seed_cast(env.store, cast=[_member("alice", HEADS_DOWN), _member("bob")])
    api = _api(env)
    env.store.db.execute("INSERT INTO channel (id, name, kind) VALUES ('eng','eng','channel')")
    named = api.create_issue("checkout", "task", "API", estimate_minutes=5,
                             assignee="alice", actor="alice")
    env.engine.schedule(SlackSendEvent(
        owner_id="bob", start_tick=20,
        payload={"message_id": "m0", "channel_id": "eng", "body": "unrelated status ping"},
    ))
    env.engine.schedule(SlackSendEvent(
        owner_id="bob", start_tick=40,
        payload={"message_id": "m1", "channel_id": "eng", "body": "hey alice can you close it?"},
    ))

    driver = WorkDriver(api, ["alice"], "checkout")
    env.engine.activities.on_activity_done = driver.on_activity_done
    env.engine.on_event_done = driver.on_event_done
    driver.sweep(env.engine)
    # Advance in two halves so we can observe the state after the unrelated
    # ping but before the mention; the closes ride the engine's event hook.
    env.engine.advance(30)
    assert api.get_issue(named.id).status == "in_review"  # unrelated ping didn't close it
    env.engine.advance_to(2400)
    assert api.get_issue(named.id).status == "done"  # the mention closed it


def test_random_selection_is_seeded_and_ignores_priority(env: Env) -> None:
    # Items 2/3: selection is a seeded random pick over the whole ready pool, not
    # the priority-ordered head. Deterministic given the run seed + person + tick.
    seed_cast(env.store, cast=[_member("alice", FREE_SPIRIT)])
    api = _api(env)
    ids = [
        api.create_issue("checkout", "task", title, component="backend",
                         estimate_minutes=5, priority=prio, actor="alice").id
        for title, prio in [("A", 3), ("B", 2), ("C", 1)]
    ]

    now = env.clock.now()
    WorkDriver(api, [_member("alice", FREE_SPIRIT)], "checkout").sweep(env.engine)

    picked = api.search(assignee="alice")[0].id
    expected = random.Random(f"42:alice:{now}").choice(sorted(ids))
    assert picked == expected  # reproducible seeded choice, independent of priority


def test_perfect_selection_takes_highest_priority(env: Env) -> None:
    # Contrast to the random persona: the perfect persona works in priority order.
    seed_cast(env.store, cast=[_member("alice", PERFECT)])
    api = _api(env)
    low = api.create_issue("checkout", "task", "A", component="backend",
                           estimate_minutes=5, priority=3, actor="alice")
    high = api.create_issue("checkout", "task", "C", component="backend",
                            estimate_minutes=5, priority=1, actor="alice")

    WorkDriver(api, [_member("alice", PERFECT)], "checkout").sweep(env.engine)

    assert api.search(assignee="alice")[0].id == high.id
    assert api.get_issue(low.id).assignee_id is None


def test_free_spirit_works_a_blocked_issue(env: Env) -> None:
    # Item 3: ignoring dependencies, the NPC picks up and finishes a blocked issue
    # whose blocker is still open.
    seed_cast(env.store, cast=[_member("alice", FREE_SPIRIT)])
    api = _api(env)
    blocker = api.create_issue("checkout", "task", "Schema", estimate_minutes=5, actor="alice")
    blocked = api.create_issue("checkout", "task", "Migration", estimate_minutes=5,
                               assignee="alice", depends_on=[blocker.id], actor="alice")
    assert api.get_issue(blocked.id).status == "blocked"

    _drive(env, WorkDriver(api, ["alice"], "checkout"))

    assert api.get_issue(blocked.id).status == "done"  # worked despite the open blocker
    assert api.get_issue(blocker.id).status == "todo"  # nobody worked the blocker


def test_perfect_announces_progress(env: Env) -> None:
    # The perfect persona raises visibility: it posts a Slack status update to the
    # wired status channel when it picks up work.
    seed_cast(env.store, cast=[_member("alice", PERFECT)])
    api = _api(env)
    env.store.db.execute("INSERT INTO channel (id, name, kind) VALUES ('eng','eng','channel')")
    task = api.create_issue("checkout", "task", "API", estimate_minutes=5,
                            assignee="alice", actor="alice")

    _drive(env, WorkDriver(api, ["alice"], "checkout", status_channel="eng"))

    msgs = env.store.list_messages("eng")
    assert len(msgs) == 1  # one pickup, one announcement
    assert msgs[0].sender_id == "alice"
    assert msgs[0].body.startswith(f"Starting {task.id}")
    assert api.get_issue(task.id).status == "done"  # and the work still finished
