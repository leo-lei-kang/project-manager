"""The scripted PM and per-member personas: the machinery behind scenarios.md.

Covers the two Slack levers (close-by-name, "pick up" directive), the
priority-0 override in ticket picking, per-member persona seeding, the CLI
--persona parsing, and the flagship before/after pair: a mixed-persona board
that misses the week unmanaged and finishes at Fri 17:00 with the PM hook.
"""

from __future__ import annotations

import pytest
import typer

from pm.cli import _parse_personas
from pm.eval import evaluate
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.behavior import assignee_pickup_hook, compose
from pm.npc.cast import CAST, with_personas
from pm.npc.persona import FREE_SPIRIT, HEADS_DOWN, PERFECT
from pm.npc.reactions import react
from pm.scenarios import (
    test_single_engineer,
    test_single_engineer_with_pm,
    test_two_engineers,
    test_two_engineers_with_pm,
)
from pm.sim.clock import WEEK_END_TICK
from pm.sim.events import SlackSendEvent
from pm.sim.simulation import Simulation

MIX = {"alice": FREE_SPIRIT, "clare": HEADS_DOWN}


def _slack(body: str) -> SlackSendEvent:
    return SlackSendEvent(owner_id="pm", start_tick=0,
                          payload={"message_id": "m", "channel_id": "eng", "body": body})


def _run(module, personas, tmp_path, *, with_pm: bool):
    env = module.build(run_id="run", root=tmp_path, member_persona=personas)
    api = JiraApi(JiraRepository(env.store), env.engine)
    pickup = assignee_pickup_hook(api, module.MEMBERS, module.PROJECT_ID)
    sim = Simulation(env)
    if with_pm:
        review = module.agent_review_hook(env)
        sim.run(on_tick=compose(review, pickup))
        review(sim)  # the PM's week-end close-out, as `pm sim` does
    else:
        sim.run(on_tick=pickup)
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


# -- CLI --persona parsing ------------------------------------------------------

def test_parse_personas_uniform_and_mixed():
    assert _parse_personas("perfect", ["alice"]) is PERFECT
    mixed = _parse_personas("alice=free_spirit,clare=heads_down", ["alice", "clare"])
    assert mixed == {"alice": FREE_SPIRIT, "clare": HEADS_DOWN}

def test_parse_personas_rejects_bad_input():
    with pytest.raises(typer.BadParameter):
        _parse_personas("nope", ["alice"])
    with pytest.raises(typer.BadParameter):
        _parse_personas("bob=perfect", ["alice"])
    with pytest.raises(typer.BadParameter):
        _parse_personas("alice=nope", ["alice"])


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


# -- the before/after pairs (scenarios.md rows 3, 5, 6) -------------------------

def test_directed_free_spirit_ships_all_launch_blockers(tmp_path):
    env, api = _run(test_single_engineer_with_pm, FREE_SPIRIT, tmp_path, with_pm=True)
    tasks = api.search(project_id=test_single_engineer_with_pm.PROJECT_ID, issue_type="task")
    assert sum(1 for t in tasks if t.status == "done") == 8  # 40h week, 60h board
    # every launch blocker shipped (identify by title: a PM bump rewrites priority)
    blockers = {title for title, _ in test_single_engineer.HIGH}
    assert blockers <= {t.title for t in tasks if t.status == "done"}
    env.close()

def test_mixed_pair_misses_the_week_unmanaged(tmp_path):
    env, _ = _run(test_two_engineers, MIX, tmp_path, with_pm=False)
    report = evaluate(env.store)
    assert not report.goal_accomplished
    assert report.done_tasks < report.total_tasks
    env.close()

def test_mixed_pair_finishes_with_scripted_pm(tmp_path):
    env, api = _run(test_two_engineers_with_pm, MIX, tmp_path, with_pm=True)
    report = evaluate(env.store)
    assert report.goal_accomplished
    assert (report.done_tasks, report.last_done_tick) == (16, WEEK_END_TICK)
    assert env.store.list_messages("eng")  # the PM actually spoke
    env.close()
