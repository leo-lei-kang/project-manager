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
from pm.npc.reactions import react
from pm.scenarios import runner, test_two_engineers
from pm.sim.events import SlackSendEvent

MIX = {"alice": FREE_SPIRIT, "clare": HEADS_DOWN}


def _slack(body: str) -> SlackSendEvent:
    return SlackSendEvent(owner_id="pm", start_tick=0,
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
    assert by_id["pm"].persona is not FREE_SPIRIT  # non-members untouched

def test_with_personas_rejects_unknown_member():
    with pytest.raises(ValueError, match="unknown cast member"):
        with_personas(CAST, {"nobody": PERFECT})


# -- the Slack directive lever --------------------------------------------------

def test_pick_up_directive_bumps_named_issue(tmp_path):
    env = test_two_engineers.build(run_id="nudge", root=tmp_path)
    api = JiraApi(JiraRepository(env.store), env.engine)
    task = api.search(project_id=test_two_engineers.PROJECT_ID, issue_type="task")[0]
    assert task.priority > 0

    react(env.engine, _slack(f"Alice: please pick up {task.id} next"))
    assert api.get_issue(task.id).priority == 0
    env.close()

def test_highlight_without_pick_up_steers_nothing(tmp_path):
    # A visibility-only message (the with_agent scenario's style) must not bump.
    env = test_two_engineers.build(run_id="highlight", root=tmp_path)
    api = JiraApi(JiraRepository(env.store), env.engine)
    task = api.search(project_id=test_two_engineers.PROJECT_ID, issue_type="task")[0]

    react(env.engine, _slack(f"High-priority still open: {task.id} — please prioritize."))
    assert api.get_issue(task.id).priority == task.priority
    env.close()


# -- the unmanaged mixed board (scenarios.md row 4) ------------------------------

def test_mixed_pair_misses_the_week_unmanaged(tmp_path):
    env, _ = _run(test_two_engineers, MIX, tmp_path)
    report = evaluate(env.store)
    assert not report.goal_accomplished
    assert report.done_tasks < report.total_tasks
    env.close()
