"""NPC cast + Jira pickup + reactions: seeding, discipline matching, autonomous work."""

from __future__ import annotations

import pytest

from pm.env import Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.cast import CAST, MEMBERS, seed_cast
from pm.sim.npc import REACTIONS, WorkDriver, available_work_for, react, react_on_tick
from pm.sim.events import (
    EventType,
    JiraTicketEvent,
    MeetingEvent,
    SlackReadEvent,
    SlackSendEvent,
)
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
    assert [p.id for p in people if p.is_agent] == ["agent"]
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


def _drive(env: Env, driver: WorkDriver) -> None:
    """Kickoff sweep + completion-driven week: the runner's wiring in miniature."""
    env.engine.activities.on_activity_done = driver.on_activity_done
    driver.sweep(env.engine)
    Simulation(env).run()


def test_stakeholders_and_agent_do_not_self_take(api: JiraApi, env: Env) -> None:
    # A management-tagged task exists, but works=False people never pick up work.
    api.create_issue("checkout", "task", "Roadmap", component="management",
                     estimate_minutes=30, actor="xavier")
    WorkDriver(api, CAST, "checkout").sweep(env.engine)
    for pid in ("erin", "vera", "xavier", "agent"):
        assert api.search(assignee=pid) == []


def test_members_autonomously_complete_the_board(api: JiraApi, env: Env) -> None:
    # A cross-discipline dependency; the members work it over the week,
    # dispatched only at kickoff and on completions.
    be = api.create_issue("checkout", "task", "API", component="backend",
                          estimate_minutes=120, actor="erin")
    api.create_issue("checkout", "task", "UI", component="frontend",
                     estimate_minutes=90, depends_on=[be.id], actor="erin")
    api.create_issue("checkout", "task", "Mockups", component="design",
                     estimate_minutes=60, actor="erin")

    _drive(env, WorkDriver(api, MEMBERS, "checkout"))

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

    _drive(env, WorkDriver(api, ["alice"], "checkout"))

    assert api.get_issue(first.id).status == "done"
    assert api.get_issue(second.id).status == "done"


def test_kickoff_only_no_polling_between_completions(api: JiraApi, env: Env) -> None:
    # An issue created mid-week (after kickoff) is NOT picked up until some
    # completion sweeps the roster — there is no per-tick polling.
    first = api.create_issue("checkout", "task", "Early", estimate_minutes=30,
                             assignee="alice", actor="erin")
    driver = WorkDriver(api, ["alice"], "checkout")
    env.engine.activities.on_activity_done = driver.on_activity_done
    driver.sweep(env.engine)                     # kickoff: picks up Early
    env.engine.advance(10)
    late = api.create_issue("checkout", "task", "Late", estimate_minutes=30,
                            assignee="alice", actor="erin")
    env.engine.advance(10)
    assert api.get_issue(late.id).status == "todo"   # not polled up mid-work
    env.engine.advance(10)                       # Early completes at tick 30 → sweep
    assert api.get_issue(first.id).status == "done"
    assert api.get_issue(late.id).status == "in_progress"


def test_pickup_log_lists_open_and_pool(api: JiraApi, env: Env) -> None:
    # The pickup log shows the person's whole plate: every open assigned ticket
    # with blocked/blocking flags, plus the unassigned discipline pool.
    blocker = api.create_issue("checkout", "task", "Blocker", component="backend",
                               estimate_minutes=30, assignee="alice", actor="erin")
    dependent = api.create_issue("checkout", "task", "Dependent", component="backend",
                                 estimate_minutes=30, assignee="alice",
                                 depends_on=[blocker.id], actor="erin")
    pool_issue = api.create_issue("checkout", "task", "Pool", component="backend",
                                  estimate_minutes=30, actor="erin")
    alice = next(c for c in CAST if c.id == "alice")
    WorkDriver(api, [alice], "checkout").sweep(env.engine)

    pickup = next(e for e in env.store.read_log() if e.kind == "npc.pickup")
    assert pickup.payload["issue_key"] == blocker.id
    briefs = {t["key"]: t for t in pickup.payload["open"]}
    assert set(briefs) == {blocker.id, dependent.id}
    assert briefs[dependent.id]["blocked"] and briefs[dependent.id]["blocking"] == 0
    assert not briefs[blocker.id]["blocked"] and briefs[blocker.id]["blocking"] == 1
    assert briefs[blocker.id]["priority"] == blocker.priority
    assert [t["key"] for t in pickup.payload["pool"]] == [pool_issue.id]


def test_pick_up_directive_preempts_current_ticket(api: JiraApi, env: Env) -> None:
    # A PM "please pick up" directive lands mid-ticket: alice interrupts her
    # current ticket, works the directed one first, then resumes the original.
    current = api.create_issue("checkout", "task", "Current", estimate_minutes=60,
                               assignee="alice", actor="erin")
    urgent = api.create_issue("checkout", "task", "Urgent", estimate_minutes=30,
                              assignee="alice", actor="erin")
    driver = WorkDriver(api, ["alice"], "checkout")
    env.engine.activities.on_activity_done = driver.on_activity_done
    driver.sweep(env.engine)                     # kickoff: picks Current (lower id)
    env.engine.advance(10)
    assert api.get_issue(current.id).status == "in_progress"

    # Alice reads the directive at tick 10: the reaction bumps to priority 0,
    # then the re-sweep preempts — WorkDriver.on_event_done's wiring in miniature.
    react(env.engine, SlackReadEvent(
        owner_id="alice", start_tick=10,
        payload={"message_id": "m", "channel_id": "eng",
                 "body": f"alice please pick up {urgent.id}"}))
    driver.sweep(env.engine)
    started = env.engine.activities.started_for("alice")
    assert started is not None and started.params["issue_key"] == urgent.id

    env.engine.advance(35)                       # Urgent (30m) completes first
    assert api.get_issue(urgent.id).status == "done"
    assert api.get_issue(current.id).status == "in_progress"  # resumed
    env.engine.advance(60)                       # Current's remaining 50m
    assert api.get_issue(current.id).status == "done"


def test_read_closes_only_the_readers_in_review(api: JiraApi, env: Env) -> None:
    # A read closes the READER's parked work, not everyone named in the message.
    mine = api.create_issue("checkout", "task", "Mine", estimate_minutes=5,
                            assignee="alice", actor="erin")
    theirs = api.create_issue("checkout", "task", "Theirs", estimate_minutes=5,
                              assignee="clare", actor="erin")
    for issue, pid in ((mine, "alice"), (theirs, "clare")):
        api.transition_issue(issue.id, "in_progress", actor=pid)
        api.transition_issue(issue.id, "in_review", actor=pid)

    # An unnamed bystander's read is awareness only — closes nothing.
    react(env.engine, SlackReadEvent(
        owner_id="bob", start_tick=0,
        payload={"message_id": "m0", "channel_id": "eng",
                 "body": "alice, clare: status?"}))
    assert api.get_issue(mine.id).status == "in_review"
    assert api.get_issue(theirs.id).status == "in_review"

    react(env.engine, SlackReadEvent(
        owner_id="alice", start_tick=0,
        payload={"message_id": "m", "channel_id": "eng",
                 "body": "alice, clare: status?"}))

    assert api.get_issue(mine.id).status == "done"
    assert api.get_issue(theirs.id).status == "in_review"


def test_dm_send_schedules_reads_for_members_only(api: JiraApi, env: Env) -> None:
    # A channel with membership rows (a DM) fans out only to its members —
    # xavier's status push to the agent is read by nobody else.
    env.store.db.execute(
        "INSERT INTO channel (id, name, kind) VALUES ('dm-x-a','dm-x-a','dm')")
    for pid in ("xavier", "agent"):
        env.store.db.execute(
            "INSERT INTO channel_member (channel_id, person_id) VALUES ('dm-x-a', ?)",
            (pid,))

    react(env.engine, SlackSendEvent(
        owner_id="xavier", start_tick=0,
        payload={"message_id": "m", "channel_id": "dm-x-a",
                 "body": "PM, please post a status update — alice is blocked."}))

    reads = env.store.db.query_all("SELECT owner_id FROM event WHERE type = 'slack.read'")
    # sender excluded, agent excluded (reads via its review trigger): no reads,
    # even though the body names alice — she is not in the DM.
    assert reads == []


def test_one_message_carries_directives_for_several_readers(api: JiraApi, env: Env) -> None:
    # One channel message can address several engineers; each directive lands
    # when ITS addressee reads — alice's read bumps only her key.
    hers = api.create_issue("checkout", "task", "Hers", estimate_minutes=30,
                            assignee="alice", actor="erin")
    theirs = api.create_issue("checkout", "task", "Theirs", estimate_minutes=30,
                              assignee="clare", actor="erin")
    body = f"alice please pick up {hers.id}; clare please pick up {theirs.id}"

    react(env.engine, SlackReadEvent(
        owner_id="alice", start_tick=0,
        payload={"message_id": "m", "channel_id": "eng", "body": body}))
    assert api.get_issue(hers.id).priority == 0
    assert api.get_issue(theirs.id).priority == theirs.priority  # not until clare reads

    react(env.engine, SlackReadEvent(
        owner_id="clare", start_tick=0,
        payload={"message_id": "m", "channel_id": "eng", "body": body}))
    assert api.get_issue(theirs.id).priority == 0


def test_directive_read_bumps_and_preempts_end_to_end(api: JiraApi, env: Env) -> None:
    # Full chain through the engine hooks: send at tick 10 → alice reads within
    # the hour → bump to priority 0 → her current ticket is interrupted, the
    # directed one finishes first, the original resumes and completes.
    env.store.db.execute("INSERT INTO channel (id, name, kind) VALUES ('eng','eng','channel')")
    current = api.create_issue("checkout", "task", "Current", estimate_minutes=200,
                               assignee="alice", actor="erin")
    urgent = api.create_issue("checkout", "task", "Urgent", estimate_minutes=30,
                              assignee="alice", actor="erin")
    driver = WorkDriver(api, ["alice"], "checkout")
    env.engine.activities.on_activity_done = driver.on_activity_done
    env.engine.on_event_done = driver.on_event_done
    driver.sweep(env.engine)                     # kickoff: picks Current
    env.engine.schedule(SlackSendEvent(
        owner_id="agent", start_tick=10,
        payload={"message_id": "m", "channel_id": "eng",
                 "body": f"alice please pick up {urgent.id}"}))

    env.engine.advance(400)                      # past the read window + both estimates

    reads = env.store.db.query_all(
        "SELECT * FROM event WHERE type = 'slack.read' AND status = 'done'")
    assert "alice" in {r["owner_id"] for r in reads}  # whole channel reads
    alice_read = next(r for r in reads if r["owner_id"] == "alice")
    assert 10 < alice_read["done_tick"] <= 70    # read within the hour of the send
    assert api.get_issue(urgent.id).priority == 0
    assert "activity.interrupt" in {e.kind for e in env.store.read_log()}
    done_urgent, done_current = api.get_issue(urgent.id), api.get_issue(current.id)
    assert done_urgent.status == "done" and done_current.status == "done"
    assert done_urgent.updated_tick < done_current.updated_tick  # urgent finished first


def test_slack_read_defers_until_the_meeting_ends(api: JiraApi, env: Env) -> None:
    # A read that would land mid-meeting yields past it at schedule time: alice
    # reads right after the meeting ends, and the directive's preempt fires then.
    api.create_issue("checkout", "task", "Current", estimate_minutes=200,
                     assignee="alice", actor="erin")
    urgent = api.create_issue("checkout", "task", "Urgent", estimate_minutes=30,
                              assignee="alice", actor="erin")
    driver = WorkDriver(api, ["alice"], "checkout")
    env.engine.activities.on_activity_done = driver.on_activity_done
    env.engine.on_event_done = driver.on_event_done
    driver.sweep(env.engine)                     # kickoff: picks Current
    env.engine.schedule(MeetingEvent(
        owner_id="erin", start_tick=5, duration=30,           # occupies ticks 5-35
        payload={"meeting_id": "m1", "kind": "adhoc", "attendees": ["alice"]}))
    env.engine.schedule(SlackReadEvent(
        owner_id="alice", start_tick=10,
        payload={"message_id": "m", "channel_id": "eng",
                 "body": f"alice please pick up {urgent.id}"}))

    env.engine.advance(45)

    read = env.store.db.query_one("SELECT * FROM event WHERE type = 'slack.read'")
    assert read["start_tick"] == 35              # rescheduled past the meeting, once
    assert read["done_tick"] == 35
    started = env.engine.activities.started_for("alice")
    assert started is not None and started.params["issue_key"] == urgent.id


def test_completion_sweep_redispatches_unblocked_partner(api: JiraApi, env: Env) -> None:
    # Alice finishing unblocks clare's dependent issue in the same sweep.
    be = api.create_issue("checkout", "task", "API", estimate_minutes=20,
                          assignee="alice", actor="erin")
    fe = api.create_issue("checkout", "task", "UI", estimate_minutes=20,
                          assignee="clare", depends_on=[be.id], actor="erin")
    assert api.get_issue(fe.id).status == "blocked"

    _drive(env, WorkDriver(api, ["alice", "clare"], "checkout"))

    assert api.get_issue(be.id).status == "done"
    assert api.get_issue(fe.id).status == "done"


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
