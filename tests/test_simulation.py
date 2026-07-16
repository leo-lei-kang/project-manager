"""Work-week calendar + the bounded simulation loop (Mon 09:00 -> Fri 17:00)."""

from __future__ import annotations

import pytest

from pm.env.environment import Env
from pm.sim.clock import TICKS_PER_WEEK, WEEK_END_TICK, format_tick, weekday
from pm.sim.events import SlackSendEvent
from pm.sim.simulation import Simulation
from pm.world.models import Person


@pytest.fixture
def env(tmp_path):
    e = Env.make(run_id="test", seed=1, root=tmp_path)
    yield e
    e.close()


def _seed_people_channel(store) -> None:
    store.add_person(Person(id="u1", name="Priya", role="Engineer"))
    store.db.execute("INSERT INTO channel (id, name, kind) VALUES ('c1', 'c1', 'channel')")


# -- calendar ----------------------------------------------------------------


def test_calendar_maps_ticks_to_workweek() -> None:
    assert format_tick(0) == "Mon 09:00"  # week starts Monday 09:00
    assert format_tick(479) == "Mon 16:59"  # last minute before end of Monday
    assert format_tick(480) == "Tue 09:00"  # 17:00 rolls straight to next 09:00
    assert format_tick(490) == "Tue 09:10"
    assert format_tick(TICKS_PER_WEEK) == "Fri 17:00"  # terminal boundary
    assert weekday(485) == "Tue"
    assert TICKS_PER_WEEK == 2400


# -- bounded loop ------------------------------------------------------------


def test_run_advances_to_friday_1700(env) -> None:
    sim = Simulation(env)
    assert sim.now_label() == "Mon 09:00"
    assert not sim.is_over()

    summary = sim.run()

    assert env.clock.now() == WEEK_END_TICK == 2400
    assert sim.is_over()
    assert summary.final_tick == 2400
    week_end = [e for e in env.store.read_log() if e.kind == "week.end"]
    assert len(week_end) == 1  # exactly one terminal marker


def test_events_inside_week_fire_and_past_week_dropped(env) -> None:
    _seed_people_channel(env.store)
    sim = Simulation(env)

    inside = sim.schedule(
        SlackSendEvent(
            owner_id="u1", start_tick=500,
            payload={"message_id": "m1", "channel_id": "c1", "body": "hi"},
        )
    )
    dropped = sim.schedule(
        SlackSendEvent(
            owner_id="u1", start_tick=2500,  # past Fri 17:00
            payload={"message_id": "m2", "channel_id": "c1", "body": "too late"},
        )
    )
    assert inside is not None
    assert dropped is None  # refused

    sim.run()

    assert [m.body for m in env.store.list_messages("c1")] == ["hi"]
    assert "event.dropped_past_week" in [e.kind for e in env.store.read_log()]


def test_delay_across_5pm_lands_next_morning(env) -> None:
    _seed_people_channel(env.store)
    sim = Simulation(env)
    # Begins Mon 16:50 (tick 470); a 20-tick send delay finishes at tick 490.
    sim.schedule(
        SlackSendEvent(
            owner_id="u1", start_tick=470, duration=20,
            payload={"message_id": "m1", "channel_id": "c1", "body": "morning"},
        )
    )
    sim.run()
    msg = env.store.list_messages("c1")[0]
    assert format_tick(msg.sent_tick) == "Tue 09:10"  # crossed 5pm, landed next morning


def test_perform_action_is_clamped_to_week_end(env) -> None:
    sim = Simulation(env)
    env.clock.advance(2390)  # Fri 16:50
    sim.perform_action(actor="agent", cost=1000)  # would run far past Friday
    assert env.clock.now() == WEEK_END_TICK  # clamped, no work after 5pm Friday


def test_on_tick_hook_can_schedule_into_the_week(env) -> None:
    _seed_people_channel(env.store)
    sim = Simulation(env)

    def hook(s: Simulation) -> None:
        if s.clock.now() == 0:  # fires once, before the first minute
            s.schedule(
                SlackSendEvent(
                    owner_id="u1", start_tick=100,
                    payload={"message_id": "m1", "channel_id": "c1", "body": "hooked"},
                )
            )

    sim.run(on_tick=hook)
    assert [m.body for m in env.store.list_messages("c1")] == ["hooked"]
