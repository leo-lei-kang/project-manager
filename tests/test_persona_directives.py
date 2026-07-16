"""Per-member personas and the Slack directive levers behind scenarios.md.

Covers the two Slack levers the world reacts to (close-by-name, "pick up"
directive with its priority-0 override), per-member persona seeding, and the
unmanaged mixed-persona board that misses the week.
"""

from __future__ import annotations

import pytest

from pm.eval import evaluate
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.cast import CAST, with_personas
from pm.npc.persona import FREE_SPIRIT, HEADS_DOWN, PERFECT
from pm.sim.npc import react
from pm.scenarios import runner, two_engineers
from pm.sim.events import SlackReadEvent, SlackSendEvent

MIX = {"alice": FREE_SPIRIT, "clare": HEADS_DOWN}


def _slack(body: str, sender: str = "agent") -> SlackSendEvent:
    return SlackSendEvent(owner_id=sender, start_tick=0,
                          payload={"message_id": "m", "channel_id": "eng", "body": body})


def _read(body: str, reader: str = "alice") -> SlackReadEvent:
    return SlackReadEvent(owner_id=reader, start_tick=0,
                          payload={"message_id": "m", "channel_id": "eng", "body": body})


def _run(module, personas, tmp_path):
    env = module.build(run_id="run", root=tmp_path, member_persona=personas)
    api = JiraApi(JiraRepository(env.store), env.engine)
    runner.drive(env, module)
    return env, api


# -- persona seeding ----------------------------------------------------------

def test_with_personas_mixed_assignment():
    cast = with_personas(CAST, {"alice": FREE_SPIRIT, "clare": HEADS_DOWN})
    by_id = {c.id: c for c in cast}
    assert by_id["alice"].persona is FREE_SPIRIT
    assert by_id["clare"].persona is HEADS_DOWN
    assert by_id["bob"].persona is CAST[1].persona  # unnamed member keeps default
    assert by_id["agent"].persona is not FREE_SPIRIT  # non-members untouched

def test_with_personas_rejects_unknown_member():
    with pytest.raises(ValueError, match="unknown cast member"):
        with_personas(CAST, {"nobody": PERFECT})


# -- the Slack directive lever --------------------------------------------------

def test_pick_up_directive_bumps_named_issue(tmp_path):
    # The bump lands when the named person READS the directive, not when it is sent.
    env = two_engineers.build(run_id="nudge", root=tmp_path)
    api = JiraApi(JiraRepository(env.store), env.engine)
    task = api.search(project_id=two_engineers.PROJECT_ID, issue_type="task")[0]
    assert task.priority > 0

    react(env.engine, _read(f"Alice: please pick up {task.id} next"))
    assert api.get_issue(task.id).priority == 0
    env.close()

def test_highlight_without_pick_up_steers_nothing(tmp_path):
    # A visibility-only message (the with_agent scenario's style) must not bump.
    env = two_engineers.build(run_id="highlight", root=tmp_path)
    api = JiraApi(JiraRepository(env.store), env.engine)
    task = api.search(project_id=two_engineers.PROJECT_ID, issue_type="task")[0]

    react(env.engine, _read(f"High-priority still open: {task.id} — please prioritize."))
    assert api.get_issue(task.id).priority == task.priority
    env.close()

def test_slack_send_schedules_reads_for_named_only_excluding_sender(tmp_path):
    # A send schedules one delayed read per named person; the sender never reads
    # their own message, and nothing is bumped at send time.
    import random as _random

    env = two_engineers.build(run_id="reads", root=tmp_path)
    api = JiraApi(JiraRepository(env.store), env.engine)
    task = api.search(project_id=two_engineers.PROJECT_ID, issue_type="task")[0]

    react(env.engine, _slack(f"Alice: please pick up {task.id} next"))
    reads = env.store.db.query_all("SELECT * FROM event WHERE type = 'slack.read'")
    assert [r["owner_id"] for r in reads] == ["alice"]
    delay = _random.Random("42:alice:0").randint(1, 60)
    assert reads[0]["start_tick"] == delay and 0 < delay <= 60
    assert api.get_issue(task.id).priority == task.priority  # no instant bump

    react(env.engine, _slack("alice here, taking a break", sender="alice"))
    reads = env.store.db.query_all("SELECT * FROM event WHERE type = 'slack.read'")
    assert len(reads) == 1  # the self-mention scheduled nothing
    env.close()


# -- the unmanaged mixed board (scenarios.md row 3) ------------------------------

def test_mixed_pair_misses_the_week_unmanaged(tmp_path):
    env, _ = _run(two_engineers, MIX, tmp_path)
    report = evaluate(env.store)
    assert not report.goal_accomplished
    assert report.done_tasks < report.total_tasks
    env.close()
