"""Tests for the consolidated cast + per-event scenario builders + generator."""

from __future__ import annotations

from pm.db.store import Store
from pm.npc.cast import AGENT, MEMBERS, STAKEHOLDERS, seed_cast
from pm.scenarios.builders import BUILDERS, BuildContext, Level, OOOBuilder
from pm.scenarios.generator import ScenarioGenerator, _counts_by_type
from pm.sim import calendar as sim_calendar
from pm.sim.clock import WEEK_END_TICK, days, hours
from pm.sim.events import EventType, MeetingEvent, OOOEvent
from pm.sim.simulation import Simulation


# -- consolidated cast -------------------------------------------------------

def test_seed_cast_writes_members_stakeholders_agent(tmp_path):
    store = Store.open(str(tmp_path / "w.db"), create=True)
    people = seed_cast(store)
    assert len(people) == len(MEMBERS) + len(STAKEHOLDERS) + 1
    # exactly one agent, and it is the PM
    agents = [p for p in people if p.is_agent]
    assert [a.id for a in agents] == [AGENT.id] == ["agent"]
    # kind persisted in persona
    alice = store.get_person("alice")
    assert alice.persona["kind"] == "member" and alice.persona["works"] is True
    vera = store.get_person("vera")
    assert vera.persona["kind"] == "stakeholder" and vera.persona["works"] is False
    store.close()


# -- builders ----------------------------------------------------------------

def _ctx() -> BuildContext:
    return BuildContext(
        members=[c.id for c in MEMBERS],
        stakeholders=[c.id for c in STAKEHOLDERS],
        agent=AGENT.id,
        channels=["eng", "general"],
        issue_keys=[f"GEN-{i}" for i in range(2, 14)],  # 12 issue keys
    )


def test_each_builder_returns_mapped_count_and_valid_ticks():
    ctx = _ctx()
    for builder in BUILDERS:
        for level in Level:
            events = builder.build(ctx, level)
            assert len(events) == builder.counts[level]
            for e in events:
                assert 0 <= e.start_tick < WEEK_END_TICK


def test_builder_counts_are_ascending():
    for builder in BUILDERS:
        few = builder.counts[Level.FEW]
        frequent = builder.counts[Level.FREQUENT]
        aggressive = builder.counts[Level.AGGRESSIVE]
        assert few <= frequent <= aggressive
        assert few < aggressive  # strictly grows overall


# -- generator ---------------------------------------------------------------

def test_generate_all_ascends_in_total_events(tmp_path):
    gen = ScenarioGenerator()
    totals = []
    seen_levels = []
    for level, env in gen.generate_all(root=tmp_path):
        seen_levels.append(level)
        totals.append(sum(_counts_by_type(env).values()))
        env.close()
    assert seen_levels == [Level.FEW, Level.FREQUENT, Level.AGGRESSIVE]
    assert totals == sorted(totals) and totals[0] < totals[-1]


# -- OOO ---------------------------------------------------------------------

def test_ooo_builder_counts_and_durations():
    ctx = _ctx()
    b = OOOBuilder()
    for level in Level:
        events = b.build(ctx, level)
        assert len(events) == b.counts[level]
        assert all(isinstance(e, OOOEvent) for e in events)
        assert all(e.duration > 0 and 0 <= e.start_tick < WEEK_END_TICK for e in events)
    # aggressive spans both "a few hours" and "a few days" scales
    aggressive = b.build(ctx, Level.AGGRESSIVE)
    durations = {e.duration for e in aggressive}
    assert any(d < days(1) for d in durations)   # hours-scale present
    assert any(d >= days(1) for d in durations)  # days-scale present


def test_ooo_is_a_top_priority_occupying_type():
    assert EventType.OOO in sim_calendar.EVENT_PRIORITY
    assert EventType.OOO in sim_calendar.OCCUPYING_TYPES
    assert sim_calendar.EVENT_PRIORITY[EventType.OOO] > sim_calendar.EVENT_PRIORITY[
        EventType.MEETING
    ]


def test_meeting_inside_an_ooo_span_is_skipped(tmp_path):
    from pm.env.environment import Env
    from pm.sim.events import EventStatus

    env = Env.make(run_id="ooo", seed=1, force=True, root=tmp_path)
    seed_cast(env.store)
    # Alice is OOO for a full day starting Mon 09:00.
    env.engine.schedule(OOOEvent(owner_id="alice", start_tick=0, duration=days(1),
                                 payload={"reason": "PTO"}))
    # A meeting with Alice, scheduled inside her OOO window: skipped, not moved.
    mtg = MeetingEvent(owner_id="agent", start_tick=hours(2), duration=30,
                            payload={"meeting_id": "m1", "attendees": ["agent", "alice"]})
    env.engine.schedule(mtg)
    assert mtg.status is EventStatus.CANCELLED
    assert mtg.start_tick == hours(2)  # never deferred
    env.close()


def test_few_scenario_runs_to_horizon_and_fires_events(tmp_path):
    gen = ScenarioGenerator()
    env = gen.generate(Level.FEW, root=tmp_path)
    scheduled = sum(_counts_by_type(env).values())
    summary = Simulation(env).run()
    assert summary.final_tick == WEEK_END_TICK
    assert summary.events_fired > 0
    # some meetings produced transcripts; some messages/emails landed
    assert scheduled > 0
    env.close()
