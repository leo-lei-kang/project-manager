"""Simulation engine — owns time progression and the sync/async boundary.

This is where the deliverable's central systems choice is made legible:

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
from pm.sim.activity import ActivityManager
from pm.sim.clock import SimClock
from pm.sim.events import Event
from pm.sim.scheduler import Scheduler


class Engine:
    def __init__(
        self,
        store: Store,
        clock: SimClock | None = None,
        scheduler: Scheduler | None = None,
    ) -> None:
        self.store = store
        self.clock = clock or SimClock(store)
        self.scheduler = scheduler or Scheduler(store, self.clock)
        # Per-NPC durative-work scheduler; a no-op until activities are requested.
        self.activities = ActivityManager(self)

    def schedule(self, event: Event) -> int:
        """Queue an event on the scheduler (convenience passthrough)."""
        return self.scheduler.schedule(event)

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
        """Advance exactly one simulated minute, updating the event queue.

        Order within the tick: first *start* every event that is now due to begin,
        then *complete* every active event whose remaining time has hit zero. An
        instantaneous event (``duration == 0``) therefore starts and finishes in the
        same tick. Returns the events that transitioned this minute.
        """
        now = self.clock.advance(1)
        transitioned: list[Event] = []
        for event in self.store.pending_events_starting_at(now):
            event.start(self)
            transitioned.append(event)
        for event in self.store.active_events():
            if event.is_finished(now):
                event.done(self)
                self.store.clear_occupancy(event.id)  # free the NPC's calendar block
                transitioned.append(event)
        # Advance durative activities (burn down, complete, resume) for this minute.
        self.activities.tick(now)
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
