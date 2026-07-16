"""Event scheduler — the home of asynchronous background activity.

The persisted ``event`` table is the queue: it is indexed on
``(status, start_tick, seq)`` and is the single source of truth for what will
happen and when. We deliberately do *not* keep a parallel in-memory heap, which
would duplicate state the architecture keeps in SQLite.

The scheduler adds one thing over the raw store: it assigns a monotonic ``seq``
so events sharing a ``start_tick`` begin in a stable, deterministic order (and
stamps ``created_tick``). Ordering across the whole queue is therefore
``(start_tick, seq)`` — reproducible across replays and resumes.
"""

from __future__ import annotations

from pm.db.store import Store
from pm.sim import calendar
from pm.sim.clock import SimClock
from pm.sim.events import Event


class Scheduler:
    def __init__(self, store: Store, clock: SimClock) -> None:
        self._store = store
        self._clock = clock
        # Resume the counter past any events already persisted, so seq is never reused.
        self._next_seq = store.max_event_seq() + 1

    def schedule(self, event: Event) -> int:
        """Queue an :class:`Event` to begin at its ``start_tick``. Returns its row id.

        The event carries its own type, actor, start tick, duration and payload; the
        scheduler only stamps the deterministic ordering fields and persists it.
        """
        now = self._clock.now()
        if event.start_tick < now:
            raise ValueError(
                f"cannot schedule event in the past: start_tick={event.start_tick} < now={now}"
            )
        event.seq = self._next_seq
        self._next_seq += 1
        event.created_tick = now
        event_id = self._store.upsert_event(event)
        # Resolve calendar contention (a durative event may be shifted here, or may
        # bump others). Instantaneous events reserve nothing and are untouched.
        calendar.reserve(self._store, event, now)
        return event_id

    def next_event_tick(self) -> int | None:
        """Earliest pending ``start_tick``, or ``None`` when nothing is pending."""
        return self._store.next_event_start_tick()

    def pending_count(self) -> int:
        return self._store.count_pending_events()

    def active_count(self) -> int:
        return self._store.count_active_events()
