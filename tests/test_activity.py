"""Activities: attenders (>=1), states + duration, per-kind effects, interrupt/resume."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pm.env.environment import Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.sim.activity import Activity
from pm.world.models import Person, Project


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
    coffee = m.request("coffee_break", ["priya"], 15, now=0)  # priority 40 too
    assert m.get(coffee.id).state == "backlogged"     # equal priority: no interrupt, the break waits


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


# -- jira_work tracks the issue named in its params ---------------------------


@pytest.fixture
def api(env) -> JiraApi:
    env.store.add_project(Project(id="checkout", name="Checkout"))
    repo = JiraRepository(env.store)
    repo.ensure_schema()
    return JiraApi(repo, env.engine)


def test_jira_work_transitions_issue_to_done(env, api):
    issue = api.create_issue("checkout", "task", "API", estimate_minutes=5,
                             assignee="priya", actor="priya")
    env.engine.activities.request("jira_work", ["priya"], 5, now=0,
                                  params={"issue_key": issue.id})
    assert api.repo.get_issue(issue.id).status == "in_progress"
    env.engine.advance(5)
    done = api.repo.get_issue(issue.id)
    assert done.status == "done" and done.remaining_minutes == 0


def test_jira_work_when_asked_parks_in_review(env, api):
    issue = api.create_issue("checkout", "task", "API", estimate_minutes=5,
                             assignee="priya", actor="priya")
    env.engine.activities.request("jira_work", ["priya"], 5, now=0,
                                  params={"issue_key": issue.id, "auto_close": False})
    env.engine.advance(5)
    assert api.repo.get_issue(issue.id).status == "in_review"


def test_jira_work_survives_interruption(env, api):
    issue = api.create_issue("checkout", "task", "API", estimate_minutes=60,
                             assignee="priya", actor="priya")
    m = env.engine.activities
    w = m.request("jira_work", ["priya"], 60, now=0, params={"issue_key": issue.id})
    env.engine.advance(30)
    m.request("meeting", ["priya"], 30, now=env.clock.now(), params={"meeting_id": "m1"})
    assert m.get(w.id).state == "interrupted"
    assert api.repo.get_issue(issue.id).status == "in_progress"  # resume won't re-transition
    env.engine.advance(60)                       # meeting 30 + remaining work 30
    assert m.get(w.id).state == "done"
    assert api.repo.get_issue(issue.id).status == "done"


# -- completion hook ----------------------------------------------------------


def test_on_activity_done_fires_once_per_completion(env):
    seen = []
    env.engine.activities.on_activity_done = lambda engine, a: seen.append(a.id)
    a = env.engine.activities.request("jira_work", ["priya"], 3, now=0)
    env.engine.advance(10)
    assert seen == [a.id]
    done = env.engine.activities.get(a.id)
    assert done.state == "done" and done.done_tick == 3


def test_on_activity_done_can_chain_next_work(env):
    m = env.engine.activities

    def chain(engine, a):
        if a.kind == "jira_work" and not a.params.get("chained"):
            m.request("jira_work", a.attendees, 4, engine.clock.now(),
                      params={"chained": True})

    m.on_activity_done = chain
    m.request("jira_work", ["priya"], 3, now=0)
    env.engine.advance(3)
    nxt = m.started_for("priya")
    assert nxt is not None and nxt.params == {"chained": True}
    env.engine.advance(4)
    assert m.get(nxt.id).state == "done"


def test_hook_request_does_not_resurrect_interrupted_work(env):
    m = env.engine.activities
    w = m.request("jira_work", ["priya"], 10, now=0)
    brk = m.request("coffee_break", ["marco"], 2, now=0)

    # When marco's break ends, the hook asks for a meeting that must interrupt
    # priya's started work — the burn loop's stale snapshot must not undo it.
    def steal(engine, a):
        if a.id == brk.id:
            m.request("meeting", ["priya"], 5, engine.clock.now(), params={"meeting_id": "m1"})

    m.on_activity_done = steal
    env.engine.advance(2)
    assert m.get(w.id).state == "interrupted"
    assert m.started_for("priya").kind == "meeting"


# -- meeting/OOO event bridge --------------------------------------------------


def test_meeting_event_interrupts_activity_work_and_it_resumes(env):
    from pm.sim.events import MeetingEvent

    m = env.engine.activities
    w = m.request("jira_work", ["priya"], 120, now=0)
    env.engine.schedule(MeetingEvent(
        owner_id="marco", start_tick=30, duration=30,
        payload={"meeting_id": "m1", "attendees": ["priya"]},
    ))
    env.engine.advance(30)                     # meeting starts; bridge preempts work
    assert m.get(w.id).state == "interrupted"
    assert m.started_for("priya").kind == "meeting"
    env.engine.advance(30)                     # meeting ends; work resumes
    assert m.started_for("priya").id == w.id
    env.engine.advance(90)                     # remaining work
    done = m.get(w.id)
    assert done.state == "done" and done.done_tick == 150  # estimate + meeting


def test_meeting_bridge_writes_no_duplicate_rows(env):
    from pm.sim.events import MeetingEvent

    env.engine.schedule(MeetingEvent(
        owner_id="marco", start_tick=1, duration=10,
        payload={"meeting_id": "m1", "attendees": ["priya"]},
    ))
    env.engine.advance(11)
    assert env.store.db.query_one("SELECT COUNT(*) AS n FROM meeting")["n"] == 1
    assert env.store.db.query_one("SELECT COUNT(*) AS n FROM transcript")["n"] == 1


def test_ooo_event_preempts_activity_work(env):
    from pm.sim.events import OOOEvent

    m = env.engine.activities
    w = m.request("jira_work", ["priya"], 60, now=0)
    env.engine.schedule(OOOEvent(owner_id="priya", start_tick=10, duration=20))
    env.engine.advance(10)
    assert m.get(w.id).state == "interrupted"
    assert m.started_for("priya").kind == "ooo"
    env.engine.advance(70)                     # ooo 20 + remaining 50
    assert m.get(w.id).state == "done"


def test_completion_hook_fires_at_meeting_end(env):
    from pm.sim.events import MeetingEvent

    seen = []
    env.engine.activities.on_activity_done = lambda engine, a: seen.append(
        (a.kind, engine.clock.now()))
    env.engine.schedule(MeetingEvent(
        owner_id="marco", start_tick=1, duration=10,
        payload={"meeting_id": "m1", "attendees": ["priya"]},
    ))
    env.engine.advance(11)
    assert ("meeting", 11) in seen


# -- idle-filler -------------------------------------------------------------


def test_default_coffee_filler_breaks_idle_people_on_a_cadence(env):
    # The engine installs coffee_filler by default: an idle person takes a
    # 10-minute break, then not another until the 2-hour cadence elapses.
    env.engine.advance(1)
    brk = env.engine.activities.started_for("priya")
    assert brk is not None and brk.kind == "coffee_break"
    assert brk.duration_needed == 10

    env.engine.advance(10)                     # break done at tick 11
    assert env.engine.activities.started_for("priya") is None
    env.engine.advance(119)                    # tick 130: cadence not yet elapsed
    assert env.engine.activities.started_for("priya") is None
    env.engine.advance(1)                      # tick 131: 120 min since last break
    assert env.engine.activities.started_for("priya").kind == "coffee_break"


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


def test_coffee_breaks_cap_at_three_per_day(env):
    # A fully idle person over one workday: the 120-min cadence would allow a
    # 4th break, but the daily cap stops at 3.
    env.engine.advance(480)  # Monday, 09:00 -> 17:00
    breaks = env.store.db.query_all(
        "SELECT started_tick FROM activity WHERE kind = 'coffee_break' "
        "AND attendees_json LIKE '%priya%' AND started_tick < 480 ORDER BY started_tick")
    ticks = [r["started_tick"] for r in breaks]
    assert len(ticks) == 3
    assert all(b - a >= 120 for a, b in zip(ticks, ticks[1:]))  # cadence intact
