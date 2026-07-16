"""NPC cast + Jira pickup + reactions: seeding, discipline matching, autonomous work."""

from __future__ import annotations

import pytest

from pm.env import Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.behavior import (
    assignee_pickup_hook,
    available_work_for,
    dev_pickup_hook,
)
from pm.npc.cast import CAST, MEMBERS, seed_cast
from pm.npc.reactions import REACTIONS, react_on_tick
from pm.sim.events import EventType, JiraTicketEvent
from pm.sim.simulation import Simulation
from pm.world.models import Project


@pytest.fixture
def env(tmp_path) -> Env:
    e = Env.make(run_id="npc", root=tmp_path)
    e.store.add_project(Project(id="checkout", name="Checkout"))
    seed_cast(e.store)
    yield e
    e.close()


@pytest.fixture
def api(env: Env) -> JiraApi:
    repo = JiraRepository(env.store)
    repo.ensure_schema()
    return JiraApi(repo, env.engine)


def test_seed_cast_writes_members_stakeholders_and_agent(api: JiraApi) -> None:
    people = api.repo.store.list_people()
    assert {p.id for p in people} == {c.id for c in CAST}
    # exactly one agent (the PM under test); everyone else is an NPC
    assert [p.id for p in people if p.is_agent] == ["pm"]
    alice = api.repo.store.get_person("alice")
    assert alice is not None and alice.persona["discipline"] == "backend"


def test_available_work_matches_discipline(api: JiraApi) -> None:
    api.create_issue("checkout", "task", "API", component="backend",
                     estimate_minutes=60, actor="erin")
    api.create_issue("checkout", "task", "UI", component="frontend",
                     estimate_minutes=60, actor="erin")
    assert [i.title for i in available_work_for(api, "checkout", "backend")] == ["API"]
    assert [i.title for i in available_work_for(api, "checkout", "frontend")] == ["UI"]


def test_jira_ticket_event_completes_and_unblocks(api: JiraApi) -> None:
    engine = api.engine
    be = api.create_issue("checkout", "task", "API", component="backend",
                          estimate_minutes=5, assignee="alice", actor="erin")
    fe = api.create_issue("checkout", "task", "UI", component="frontend",
                          estimate_minutes=5, assignee="clare",
                          depends_on=[be.id], actor="erin")
    assert api.get_issue(fe.id).status == "blocked"

    engine.schedule(JiraTicketEvent(owner_id="alice", start_tick=1, duration=5,
                                   payload={"issue_key": be.id}))
    engine.advance(1)
    assert api.get_issue(be.id).status == "in_progress"

    engine.advance_to(6)
    done = api.get_issue(be.id)
    assert done.status == "done" and done.remaining_minutes == 0
    assert api.get_issue(fe.id).status == "todo"  # dependent auto-unblocked


def test_stakeholders_and_agent_do_not_self_take(api: JiraApi, env: Env) -> None:
    # A management-tagged task exists, but works=False people never pick up work.
    api.create_issue("checkout", "task", "Roadmap", component="management",
                     estimate_minutes=30, actor="xavier")
    dev_pickup_hook(api, CAST, "checkout")(Simulation(env))
    for pid in ("erin", "vera", "xavier", "pm"):
        assert api.search(assignee=pid) == []


def test_members_autonomously_complete_the_board(api: JiraApi, env: Env) -> None:
    # A cross-discipline dependency; the members work it over the week.
    be = api.create_issue("checkout", "task", "API", component="backend",
                          estimate_minutes=120, actor="erin")
    api.create_issue("checkout", "task", "UI", component="frontend",
                     estimate_minutes=90, depends_on=[be.id], actor="erin")
    api.create_issue("checkout", "task", "Mockups", component="design",
                     estimate_minutes=60, actor="erin")

    Simulation(env).run(on_tick=dev_pickup_hook(api, MEMBERS, "checkout"))

    statuses = {i.title: i.status for i in api.search(project_id="checkout")}
    assert statuses == {"API": "done", "UI": "done", "Mockups": "done"}
    # worked by the right disciplines (two backend members share the backend pool)
    assert api.search(project_id="checkout", component="backend")[0].assignee_id in {"alice", "david"}
    assert api.search(project_id="checkout", assignee="clare")[0].title == "UI"
    assert api.search(project_id="checkout", assignee="elieen")[0].title == "Mockups"


def test_assignee_pickup_works_assigned_chain(api: JiraApi, env: Env) -> None:
    # A pre-assigned board (no components): one person's dependent chain completes.
    first = api.create_issue("checkout", "task", "Schema", estimate_minutes=60,
                             assignee="alice", actor="erin")
    second = api.create_issue("checkout", "task", "Migration", estimate_minutes=60,
                              assignee="alice", depends_on=[first.id], actor="erin")
    assert api.get_issue(second.id).status == "blocked"

    Simulation(env).run(on_tick=assignee_pickup_hook(api, ["alice"], "checkout"))

    assert api.get_issue(first.id).status == "done"
    assert api.get_issue(second.id).status == "done"


def test_reactions_cover_all_event_types() -> None:
    # A hook is registered for every event type.
    assert set(REACTIONS) == set(EventType)


def test_jira_ticket_done_reaction_picks_up_next(api: JiraApi, env: Env) -> None:
    # Reactive cascade: finishing an assigned issue schedules the owner's next one.
    first = api.create_issue("checkout", "task", "A", estimate_minutes=5,
                             assignee="alice", actor="erin")
    second = api.create_issue("checkout", "task", "B", estimate_minutes=5,
                              assignee="alice", depends_on=[first.id], actor="erin")
    assert api.get_issue(second.id).status == "blocked"

    # Bootstrap the first piece of work; the reaction carries the rest.
    env.engine.schedule(JiraTicketEvent(owner_id="alice", start_tick=1, duration=5,
                                       payload={"issue_key": first.id}))
    Simulation(env).run(on_tick=react_on_tick)

    assert api.get_issue(first.id).status == "done"
    assert api.get_issue(second.id).status == "done"  # picked up reactively
