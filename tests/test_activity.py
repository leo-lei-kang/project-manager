"""Activities: attenders (>=1), states + duration, per-kind effects, interrupt/resume."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pm.env.environment import Env
from pm.sim.activity import Activity
from pm.world.models import Person


@pytest.fixture
def env(tmp_path):
    e = Env.make(run_id="act", seed=1, root=tmp_path)
    for pid in ("priya", "marco"):
        e.store.add_person(Person(id=pid, name=pid))
    e.store.db.execute("INSERT INTO channel (id, name, kind) VALUES ('c1','c1','channel')")
    yield e
    e.close()


# -- at least one attender ---------------------------------------------------


def test_activity_requires_an_attender():
    with pytest.raises(ValidationError):
        Activity(kind="coffee_break", attendees=[], priority=5,
                 duration_needed=10, remaining=10)


def test_request_with_empty_attendees_raises(env):
    with pytest.raises(ValidationError):
        env.engine.activities.request("coffee_break", [], 10, now=0)


# -- states + duration -------------------------------------------------------


def test_states_and_duration_burndown(env):
    m = env.engine.activities
    a = m.request("jira_work", ["priya"], 5, now=0)
    assert a.state == "started"                 # dispatched immediately
    env.engine.advance(3)
    assert m.get(a.id).state == "started" and m.get(a.id).remaining == 2
    env.engine.advance(2)
    assert m.get(a.id).state == "done"          # burned down to 0


# -- per-kind effects --------------------------------------------------------


def test_write_doc_produces_a_document(env):
    m = env.engine.activities
    m.request("write_doc", ["priya"], 10, now=0,
              params={"doc_id": "D1", "title": "Spec", "body": "..."})
    env.engine.advance(10)
    row = env.store.db.query_one("SELECT * FROM document WHERE id = 'D1'")
    assert row is not None and row["title"] == "Spec"


def test_slack_send_posts_a_message(env):
    m = env.engine.activities
    m.request("slack_send", ["priya"], 3, now=0,
              params={"message_id": "s1", "channel_id": "c1", "body": "shipped"})
    env.engine.advance(3)
    assert [msg.body for msg in env.store.list_messages("c1")] == ["shipped"]


def test_meeting_produces_a_transcript(env):
    m = env.engine.activities
    m.request("meeting", ["priya", "marco"], 30, now=0,
              params={"meeting_id": "m1", "transcript_id": "tr1", "transcript_body": "notes"})
    env.engine.advance(30)
    assert env.store.db.query_one("SELECT * FROM transcript WHERE id = 'tr1'") is not None


# -- one active per NPC + priority ------------------------------------------


def test_two_works_serialize_per_npc(env):
    m = env.engine.activities
    a1 = m.request("jira_work", ["priya"], 60, now=0)
    a2 = m.request("jira_work", ["priya"], 30, now=0)
    assert a1.state == "started" and m.get(a2.id).state == "backlogged"
    env.engine.advance(60)
    assert m.get(a1.id).state == "done" and m.started_for("priya").id == a2.id


def test_coffee_backlogged_behind_work(env):
    m = env.engine.activities
    m.request("jira_work", ["priya"], 60, now=0)      # priority 40
    coffee = m.request("coffee_break", ["priya"], 15, now=0)  # priority 5
    assert m.get(coffee.id).state == "backlogged"     # work outranks the break


# -- multi-attender meeting interrupts both, both resume ---------------------


def test_meeting_interrupts_all_attenders_and_they_resume(env):
    m = env.engine.activities
    w1 = m.request("jira_work", ["priya"], 120, now=0)
    w2 = m.request("jira_work", ["marco"], 120, now=0)
    assert w1.state == "started" and w2.state == "started"

    env.engine.advance(30)                            # 30 min of work each
    m.request("meeting", ["priya", "marco"], 30, now=env.clock.now())
    assert m.get(w1.id).state == "interrupted"
    assert m.get(w2.id).state == "interrupted"
    assert m.started_for("priya").kind == "meeting"
    assert m.started_for("marco").kind == "meeting"

    env.engine.advance(30)                            # meeting ends -> both resume
    assert m.started_for("priya").id == w1.id
    assert m.started_for("marco").id == w2.id

    env.engine.advance_to(180)                        # 90 left each -> done by 30+30+90+...
    assert m.get(w1.id).state == "done"
    assert m.get(w2.id).state == "done"


# -- idle-filler -------------------------------------------------------------


def test_idle_filler_enqueues_a_break(tmp_path):
    e = Env.make(run_id="idle", seed=1, root=tmp_path)
    e.store.add_person(Person(id="priya", name="priya"))

    def filler(mgr, now):
        if mgr.is_idle("priya"):
            mgr.request("coffee_break", ["priya"], 10, now)

    e.engine.activities._idle_filler = filler
    e.engine.advance(1)                               # tick calls the filler
    assert e.engine.activities.started_for("priya").kind == "coffee_break"
    e.close()
