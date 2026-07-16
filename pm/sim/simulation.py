"""The world-simulation model — a bounded event loop over the work week.

`Simulation` wraps the existing kernel (clock + engine over a `Store`) and
drives it from **Monday 09:00 to Friday 17:00**. It does not replace the engine;
behaviour still lives on the durative `Event` objects. Its job is the *loop*: advance
sim-time one work-minute at a time, starting and completing events as they come due,
until the week ends — then emit a terminal ``week.end`` marker.

The week is fixed-length: `run()` always advances to Friday 17:00
(:data:`~pm.sim.clock.WEEK_END_TICK`), firing every scheduled event along the way,
regardless of queue state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pm.sim.clock import WEEK_END_TICK

if TYPE_CHECKING:  # duck-typed to avoid a pm.sim -> pm.env import cycle
    from pm.env.environment import Env
    from pm.sim.events import Event


@dataclass
class RunSummary:
    """Outcome of a week run, for the CLI / evaluator."""

    final_tick: int
    events_fired: int


class Simulation:
    def __init__(self, env: "Env") -> None:
        self.env = env
        self.store = env.store
        self.clock = env.clock
        self.engine = env.engine

    # -- calendar-aware status ----------------------------------------------

    def now_label(self) -> str:
        """Current calendar time, e.g. ``"Wed 14:30"``."""
        return self.clock.as_clock_str()

    def is_over(self) -> bool:
        return self.clock.now() >= WEEK_END_TICK

    def remaining_ticks(self) -> int:
        return max(0, WEEK_END_TICK - self.clock.now())

    # -- guarded scheduling / actions ---------------------------------------

    def schedule(self, event: "Event") -> int | None:
        """Queue an event, unless it would begin after the week ends.

        Past-week events can never fire in a fixed-length week, so they are dropped
        (and logged) rather than silently lingering in the queue.
        """
        if event.start_tick >= WEEK_END_TICK:
            self.store.log_event(
                self.clock.now(),
                actor="engine",
                kind="event.dropped_past_week",
                payload={"type": event.type.value, "start_tick": event.start_tick},
            )
            return None
        return self.engine.schedule(event)

    def perform_action(
        self, actor: str, cost: int, effect: Callable[[], None] | None = None
    ) -> list["Event"]:
        """Agent action, with its time cost clamped so it cannot run past Fri 17:00."""
        return self.engine.perform_action(actor, min(cost, self.remaining_ticks()), effect)

    # -- the event loop ------------------------------------------------------

    def step(self) -> list["Event"]:
        """Advance one work-minute, unless the week is already over."""
        if self.is_over():
            return []
        return self.engine.step()

    def run(self, on_tick: Callable[["Simulation"], None] | None = None) -> RunSummary:
        """Run the world to Friday 17:00, one work-minute at a time.

        ``on_tick`` is an optional per-minute hook (an observer or an agent's
        scheduling logic) invoked before each minute's step. It may read state and
        ``schedule(...)`` events, but must NOT advance the clock itself — the loop owns
        time. A terminal ``week.end`` log entry is written when the week closes.
        """
        fired = 0
        while not self.is_over():
            if on_tick is not None:
                on_tick(self)
            fired += len(self.step())
        self.store.log_event(WEEK_END_TICK, actor="engine", kind="week.end")
        return RunSummary(final_tick=self.clock.now(), events_fired=fired)
