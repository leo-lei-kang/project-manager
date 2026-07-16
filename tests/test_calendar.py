"""Per-NPC occupancy calendar: priority bump/defer, multi-attendee, pause/resume."""

from __future__ import annotations

import pytest

from pm.env.environment import Env
from pm.jira.api import JiraApi
from pm.jira.models import Issue
from pm.jira.repository import JiraRepository
from pm.sim.events import MeetingEvent, SlackSendEvent, JiraTicketEvent
from pm.world.models import Person, Project


@pytest.fixture
def env(tmp_path):
    e = Env.make(run_id="cal", seed=1, root=tmp_path)
    _seed(e.store)
    yield e
    e.close()


def _seed(store) -> None:
    for pid in ("priya", "marco", "pm"):
        store.add_person(Person(id=pid, name=pid))
    store.add_project(Project(id="p1", name="P"))
    repo = JiraRepository(store)
    repo.ensure_schema()
    for tid in ("t1", "t2"):
        repo.add_issue(Issue(id=tid, project_id="p1", issue_type="task", title=tid,
                             estimate_minutes=240, remaining_minutes=240))
    store.db.execute("INSERT INTO channel (id, name, kind) VALUES ('c1', 'c1', 'channel')")


def _row(store, etype: str) -> dict:
    return dict(store.db.query_one("SELECT * FROM event WHERE type = ?", (etype,)))


def _work(actor="priya", issue="t1", start=60, dur=60) -> JiraTicketEvent:
    return JiraTicketEvent(owner_id=actor, start_tick=start, duration=dur,
                          payload={"issue_key": issue})


def _meeting(start=60, dur=30, attendees=("priya",)) -> MeetingEvent:
    return MeetingEvent(
        owner_id="pm", start_tick=start, duration=dur,
        payload={"meeting_id": "m1", "kind": "standup", "attendees": list(attendees)})


# -- priority resolution -----------------------------------------------------


def test_meeting_bumps_planned_work_later(env):
    env.engine.schedule(_work(start=60, dur=60))        # [60,120)
    env.engine.schedule(_meeting(start=60, dur=30))     # [60,90), higher priority
    assert _row(env.store, "meeting")["start_tick"] == 60   # meeting unmoved
    work = _row(env.store, "jira_ticket")
    assert work["start_tick"] == 90                     # pushed to after the meeting
    assert work["duration"] == 60                       # full work still to do


def test_work_yields_behind_existing_meeting(env):
    env.engine.schedule(_meeting(start=60, dur=30))     # [60,90)
    env.engine.schedule(_work(start=70, dur=60))        # wants [70,130) -> must yield
    assert _row(env.store, "jira_ticket")["start_tick"] == 90


def test_two_works_serialize(env):
    env.engine.schedule(_work(issue="t1", start=60, dur=60))   # [60,120)
    env.engine.schedule(_work(issue="t2", start=60, dur=30))   # yields behind t1
    rows = env.store.db.query_all(
        "SELECT payload_json, start_tick, duration FROM event WHERE type='jira_ticket' "
        "ORDER BY start_tick")
    spans = [(r["start_tick"], r["start_tick"] + r["duration"]) for r in rows]
    # No overlap between the two work blocks for the same NPC.
    assert spans[0][1] <= spans[1][0]


def test_meeting_bumps_all_attendees(env):
    env.engine.schedule(_work(actor="priya", issue="t1", start=60, dur=60))
    env.engine.schedule(_work(actor="marco", issue="t2", start=60, dur=60))
    env.engine.schedule(_meeting(start=60, dur=30, attendees=("priya", "marco")))
    starts = [r["start_tick"] for r in env.store.db.query_all(
        "SELECT start_tick FROM event WHERE type='jira_ticket'")]
    assert starts == [90, 90]  # both attendees' work pushed past the meeting


def test_half_open_boundary_no_bump(env):
    env.engine.schedule(_meeting(start=60, dur=30))     # [60,90)
    env.engine.schedule(_work(start=90, dur=30))        # [90,120) touches but doesn't overlap
    assert _row(env.store, "jira_ticket")["start_tick"] == 90  # not pushed


# -- pause & resume (active work) --------------------------------------------


def test_active_work_extended_by_meeting(env):
    api = JiraApi(JiraRepository(env.store), env.engine)
    env.engine.schedule(_work(start=0, dur=120))        # [0,120)
    env.engine.advance(1)                               # now=1: work is ACTIVE
    assert api.get_issue("t1").status == "in_progress"

    env.engine.schedule(_meeting(start=30, dur=30))     # meeting during the work
    work = _row(env.store, "jira_ticket")
    assert work["duration"] == 150                      # 120 + 30 meeting = finishes later

    env.engine.advance_to(120)                          # original finish tick
    assert api.get_issue("t1").status == "in_progress"  # not done yet (pushed out)
    env.engine.advance_to(150)
    assert api.get_issue("t1").status == "done"         # done 30 ticks later


# -- instantaneous events unaffected -----------------------------------------


def test_instantaneous_event_not_blocked_by_meeting(env):
    env.engine.schedule(_meeting(start=60, dur=30))
    env.engine.schedule(SlackSendEvent(
        owner_id="priya", start_tick=70,
        payload={"message_id": "r1", "channel_id": "c1", "body": "quick note"}))
    assert _row(env.store, "slack.send")["start_tick"] == 70  # not deferred
    env.engine.advance_to(71)
    assert [m.body for m in env.store.list_messages("c1")] == ["quick note"]


# -- determinism -------------------------------------------------------------


def test_resolution_is_deterministic(tmp_path):
    def run() -> list[tuple]:
        e = Env.make(run_id="d", seed=1, root=tmp_path, force=True)
        _seed(e.store)
        e.engine.schedule(_work(start=60, dur=60))
        e.engine.schedule(_meeting(start=60, dur=30))
        rows = [(r["type"], r["start_tick"], r["duration"]) for r in e.store.db.query_all(
            "SELECT type, start_tick, duration FROM event ORDER BY type")]
        e.close()
        return rows

    assert run() == run()
