"""Simulation engine — the event queue, time progression, and the sync/async boundary.

This is where the deliverable's central systems choice is made legible:

  * **Scheduling.** :meth:`schedule` queues an event on the persisted ``event``
    table — the queue itself, indexed on ``(status, start_tick, seq)``, with no
    parallel in-memory heap. The engine stamps a monotonic ``seq`` (resumed past
    the persisted max, so ordering is reproducible across replays and resumes)
    and resolves calendar contention at schedule time.
  * **Synchronous.** :meth:`perform_action` applies an agent's effect at the
    *current* tick — no sim-time passes while the effect runs — then advances the
    clock by the action's cost.
  * **Asynchronous.** Time advances one simulated **minute at a time**
    (:meth:`step`). Each minute the engine updates the event queue: it *starts*
    every event whose ``start_tick`` has arrived and *completes* every active event
    whose duration has elapsed. NPC chat messages, meetings, and task work are all
    durative events driven purely by the clock — never a real background thread.

Behaviour lives on the :class:`~pm.sim.events.Event` objects themselves; the engine
just moves them through ``start() -> done()`` at the right ticks.
"""

from __future__ import annotations

from collections.abc import Callable

from pm.db.store import Store
from pm.sim import calendar
from pm.sim.activity import ActivityManager
from pm.sim.clock import SimClock
from pm.sim.events import Event


class Engine:
    def __init__(self, store: Store, clock: SimClock | None = None) -> None:
        self.store = store
        self.clock = clock or SimClock(store)
        # Resume the counter past any events already persisted, so seq is never reused.
        self._next_seq = store.max_event_seq() + 1
        # Per-NPC durative-work scheduler; a no-op until activities are requested.
        self.activities = ActivityManager(self)
        # Completion hook: fired once per finished event from step(), after the
        # completion loop. The driver installs NPC reactions here.
        self.on_event_done: Callable[["Engine", Event], None] | None = None

    # -- scheduling ------------------------------------------------------------

    def schedule(self, event: Event) -> int:
        """Queue an :class:`Event` to begin at its ``start_tick``. Returns its row id.

        The event carries its own type, actor, start tick, duration and payload; the
        engine only stamps the deterministic ordering fields and persists it.
        """
        now = self.clock.now()
        if event.start_tick < now:
            raise ValueError(
                f"cannot schedule event in the past: start_tick={event.start_tick} < now={now}"
            )
        event.seq = self._next_seq
        self._next_seq += 1
        event.created_tick = now
        event_id = self.store.upsert_event(event)
        # Resolve calendar contention (a durative event may be shifted here, or may
        # bump others). Instantaneous events reserve nothing and are untouched.
        calendar.reserve(self.store, event, now)
        # Logged after reserve so start_tick reflects any calendar shift.
        self.store.log_event(now, actor=event.owner_id, kind="event.scheduled",
                             payload={"type": event.type.value,
                                      "start_tick": event.start_tick})
        return event_id

    def pending_count(self) -> int:
        return self.store.count_pending_events()

    def active_count(self) -> int:
        return self.store.count_active_events()

    # -- synchronous side ----------------------------------------------------

    def perform_action(
        self, actor: str, cost: int, effect: Callable[[], None] | None = None
    ) -> list[Event]:
        """Apply ``effect`` at the current tick, then advance time by ``cost``.

        The effect (an agent's tool mutation) resolves synchronously — no sim-time
        passes while it runs. Advancing by ``cost`` then starts/completes any
        background events that come due. Returns the events that transitioned during
        that advance so the caller can observe what the world did in response.
        """
        if effect is not None:
            effect()
        self.store.log_event(self.clock.now(), actor=actor, kind="action")
        return self.advance(cost)

    # -- asynchronous side ---------------------------------------------------

    def step(self) -> list[Event]:
        """Advance exactly one simulated minute, updating activities and events.

        Order within the tick: first *tick activities* (work ending at this minute
        completes before anything that begins at it), then *start* every event that
        is now due to begin, then *complete* every active event whose remaining time
        has hit zero. An instantaneous event (``duration == 0``) therefore starts and
        finishes in the same tick. Returns the events that transitioned this minute.
        """
        now = self.clock.advance(1)
        # Burn down activities first: work whose last minute lands exactly at an
        # event's start tick must finish before that event (e.g. a meeting bridge)
        # can interrupt it — otherwise zero-slack schedules strand a minute.
        self.activities.tick(now)
        transitioned: list[Event] = []
        for event in self.store.pending_events_starting_at(now):
            event.start(self)
            transitioned.append(event)
        finished: list[Event] = []
        for event in self.store.active_events():
            if event.is_finished(now):
                event.done(self)
                self.store.clear_occupancy(event.id)  # free the NPC's calendar block
                transitioned.append(event)
                finished.append(event)
        # Fire completion hooks after the loop (mirrors ActivityManager.tick):
        # a hook may schedule() or request() follow-up work.
        if self.on_event_done is not None:
            for event in finished:
                self.on_event_done(self, event)
        return transitioned

    def advance(self, minutes: int) -> list[Event]:
        """Advance the clock ``minutes`` ticks, stepping one minute at a time."""
        if minutes < 0:
            raise ValueError(f"cannot advance by negative minutes {minutes}")
        transitioned: list[Event] = []
        for _ in range(minutes):
            transitioned.extend(self.step())
        return transitioned

    def advance_to(self, target_tick: int) -> list[Event]:
        """Advance sim-time to ``target_tick`` (must be at or after now)."""
        now = self.clock.now()
        if target_tick < now:
            raise ValueError(
                f"cannot advance to the past: target={target_tick} < now={now}"
            )
        return self.advance(target_tick - now)
