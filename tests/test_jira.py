"""Tests for the additive Jira-style issue module (pm.jira).

Imports pm.jira directly — the package is editable-installed, so no path setup.
"""

from __future__ import annotations

import pytest

from pm.db.store import Store
from pm.exceptions import ToolError
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.sim.engine import Engine
from pm.world.models import Person, Project


@pytest.fixture()
def api(tmp_path):
    store = Store.open(str(tmp_path / "world.db"), create=True)
    engine = Engine(store)
    repo = JiraRepository(store)
    repo.ensure_schema()
    store.add_project(Project(id="checkout", name="Checkout Redesign v2"))
    store.add_person(Person(id="dana", name="Dana", role="Backend"))
    yield JiraApi(repo, engine)
    store.close()


# -- keys & creation ---------------------------------------------------------

def test_create_generates_sequential_jira_keys(api):
    a = api.create_issue("checkout", "epic", "Epic A")
    b = api.create_issue("checkout", "story", "Story B", parent=a.id)
    assert a.id == "CHECKOUT-1"
    assert b.id == "CHECKOUT-2"


def test_remaining_defaults_to_estimate_on_create(api):
    t = api.create_issue("checkout", "task", "T", estimate_minutes=90)
    assert t.estimate_minutes == 90
    assert t.remaining_minutes == 90


def test_create_rejects_unknown_project(api):
    with pytest.raises(ToolError):
        api.create_issue("nope", "task", "T")


# -- hierarchy rules ---------------------------------------------------------

def test_valid_hierarchy_epic_story_task(api):
    epic = api.create_issue("checkout", "epic", "E")
    story = api.create_issue("checkout", "story", "S", parent=epic.id)
    task = api.create_issue("checkout", "task", "T", parent=story.id)
    assert story.parent_id == epic.id
    assert task.parent_id == story.id


def test_epic_cannot_have_parent(api):
    epic = api.create_issue("checkout", "epic", "E")
    with pytest.raises(ToolError):
        api.create_issue("checkout", "epic", "E2", parent=epic.id)


def test_task_cannot_be_child_of_task(api):
    parent_task = api.create_issue("checkout", "task", "T1")
    with pytest.raises(ToolError):
        api.create_issue("checkout", "task", "T2", parent=parent_task.id)


def test_story_requires_epic_parent(api):
    story = api.create_issue("checkout", "story", "S1")  # top-level story ok
    with pytest.raises(ToolError):
        api.create_issue("checkout", "story", "S2", parent=story.id)


# -- rollups -----------------------------------------------------------------

def test_rollup_sums_estimates_over_subtree(api):
    epic = api.create_issue("checkout", "epic", "E")
    story = api.create_issue("checkout", "story", "S", parent=epic.id)
    api.create_issue("checkout", "task", "T1", parent=story.id, estimate_minutes=360)
    api.create_issue("checkout", "task", "T2", parent=story.id, estimate_minutes=120)

    roll = api.get_rollup(epic.id)
    assert roll.estimate_minutes == 480
    assert roll.remaining_minutes == 480
    assert roll.leaf_count == 2  # two tasks are the leaves


def test_rollup_tracks_remaining_after_logging_work(api):
    epic = api.create_issue("checkout", "epic", "E")
    t = api.create_issue("checkout", "task", "T", parent=epic.id, estimate_minutes=300)
    api.log_work(t.id, 100)
    roll = api.get_rollup(epic.id)
    assert roll.estimate_minutes == 300
    assert roll.remaining_minutes == 200


# -- dependencies & derived blocked ------------------------------------------

def test_link_sets_depends_on_and_blocks_dependent(api):
    blocker = api.create_issue("checkout", "task", "migrate", estimate_minutes=60)
    dependent = api.create_issue("checkout", "task", "ui", estimate_minutes=60)
    api.link_issue(dependent.id, blocker.id)  # blocker blocks dependent

    dependent = api.get_issue(dependent.id)
    assert dependent.depends_on == [blocker.id]
    assert dependent.status == "blocked"


def test_completing_blocker_unblocks_dependent(api):
    blocker = api.create_issue("checkout", "task", "migrate", estimate_minutes=60)
    dependent = api.create_issue("checkout", "task", "ui", estimate_minutes=60)
    api.link_issue(dependent.id, blocker.id)
    assert api.get_issue(dependent.id).status == "blocked"

    api.transition_issue(blocker.id, "in_progress")
    api.transition_issue(blocker.id, "done")

    assert api.get_issue(dependent.id).status == "todo"


def test_reopening_blocker_reblocks_dependent(api):
    blocker = api.create_issue("checkout", "task", "migrate")
    dependent = api.create_issue("checkout", "task", "ui")
    api.link_issue(dependent.id, blocker.id)
    api.transition_issue(blocker.id, "in_progress")
    api.transition_issue(blocker.id, "done")
    assert api.get_issue(dependent.id).status == "todo"

    api.transition_issue(blocker.id, "in_progress")  # reopen
    assert api.get_issue(dependent.id).status == "blocked"


def test_self_link_rejected(api):
    t = api.create_issue("checkout", "task", "T")
    with pytest.raises(ToolError):
        api.link_issue(t.id, t.id)


def test_cycle_link_rejected(api):
    a = api.create_issue("checkout", "task", "A")
    b = api.create_issue("checkout", "task", "B")
    api.link_issue(a.id, b.id)  # a depends on b
    with pytest.raises(ToolError):
        api.link_issue(b.id, a.id)  # b depends on a -> cycle


# -- transitions -------------------------------------------------------------

def test_illegal_transition_rejected(api):
    t = api.create_issue("checkout", "task", "T")
    with pytest.raises(ToolError):
        api.transition_issue(t.id, "done")  # todo -> done is not allowed directly


def test_legal_transition_path(api):
    t = api.create_issue("checkout", "task", "T")
    api.transition_issue(t.id, "in_progress")
    api.transition_issue(t.id, "done")
    assert api.get_issue(t.id).status == "done"


# -- worklog & engine write-through -----------------------------------------

def test_log_work_floors_at_zero(api):
    t = api.create_issue("checkout", "task", "T", estimate_minutes=60)
    api.log_work(t.id, 100)
    assert api.get_issue(t.id).remaining_minutes == 0


def test_mutations_go_through_engine_action_log(api):
    # Zero-cost board writes stay silent; a costed action leaves the audit line.
    api.create_issue("checkout", "task", "T")
    assert "action" not in [e.kind for e in api.repo.store.read_log()]

    api.action_cost = 5
    api.create_issue("checkout", "task", "U")
    entry = next(e for e in api.repo.store.read_log() if e.kind == "action")
    assert entry.payload == {"cost": 5}


# -- persistence round-trip --------------------------------------------------

def test_get_missing_issue_raises(api):
    with pytest.raises(ToolError):
        api.get_issue("CHECKOUT-999")


def test_depends_on_round_trips_through_db(api):
    a = api.create_issue("checkout", "task", "A")
    b = api.create_issue("checkout", "task", "B")
    api.link_issue(b.id, a.id)
    reloaded = api.repo.get_issue(b.id)
    assert reloaded.depends_on == [a.id]
