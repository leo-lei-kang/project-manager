"""Per-NPC time calendar — resolves contention for an NPC's time.

Every *durative* event occupies the NPC(s) it involves for its window; the
``occupancy`` table is the materialized calendar of those blocks. When a new block
is reserved, conflicts are resolved by **priority (by event type)**:

  * higher-priority incoming (a meeting) **bumps** lower-priority overlaps: planned
    work moves to after the meeting; work already *in progress* has its duration
    extended by the meeting's length (it pauses and resumes, finishing later);
  * lower-or-equal-priority incoming (work vs a meeting, or work vs earlier work)
    **yields** — it is pushed to start after the blocking window.

Instantaneous events (emails, slack) are not in
``EVENT_PRIORITY`` and reserve nothing, so they are never blocked. All resolution
happens at reserve time (called from the scheduler), so the engine simply runs
events on their resolved schedule — there is no runtime preemption.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pm.sim.events import EventStatus, EventType

if TYPE_CHECKING:
    from pm.db.store import Store
    from pm.sim.events import Event

# P1: priority by event type. Higher wins. Only occupying (durative) types listed.
EVENT_PRIORITY: dict[EventType, int] = {
    EventType.OOO: 200,  # out-of-office: top priority — a meeting never bumps it
    EventType.MEETING: 100,
    EventType.JIRA_TICKET: 20,
}
OCCUPYING_TYPES = set(EVENT_PRIORITY)


def people_for(event: "Event") -> set[str]:
    """The NPCs an event occupies: all attendees (+ organizer) of a meeting, or the
    single actor of a piece of work."""
    if event.type is EventType.MEETING:
        return set(event.payload.get("attendees", [])) | {event.owner_id}
    return {event.owner_id}


def reserve(store: "Store", event: "Event", now: int) -> None:
    """Place ``event`` on its people's calendars, resolving conflicts by priority.

    May shift ``event.start_tick`` (if it must yield) and may reschedule other events
    (if it bumps them). Assumes ``event`` is already persisted (has an ``id``).
    """
    if event.type not in OCCUPYING_TYPES:
        return  # instantaneous / non-occupying: no calendar footprint
    prio = EVENT_PRIORITY[event.type]
    people = people_for(event)

    # 1) Yield: push our start past any block of >= priority that we overlap.
    while True:
        end = event.start_tick + event.duration
        blockers = [
            o
            for o in store.occupancy_overlaps(people, event.start_tick, end)
            if o["event_id"] != event.id and o["priority"] >= prio
        ]
        if not blockers:
            break
        event.start_tick = max(o["end_tick"] for o in blockers)

    # 2) Commit our (possibly shifted) placement and lay our blocks.
    store.reschedule_event(event)
    store.clear_occupancy(event.id)
    for pid in sorted(people):
        store.add_occupancy(pid, event, prio)

    # 3) Bump every lower-priority activity we now overlap.
    end = event.start_tick + event.duration
    losers = {
        o["event_id"]: o
        for o in store.occupancy_overlaps(people, event.start_tick, end)
        if o["event_id"] != event.id and o["priority"] < prio
    }
    for lid in sorted(losers):
        loser = store.get_event(lid)
        if loser is None or loser.status in (EventStatus.DONE, EventStatus.CANCELLED):
            continue
        if loser.status is EventStatus.ACTIVE:
            # Already running: pause & resume — it finishes later by our length.
            loser.duration += event.duration
            store.reschedule_event(loser)
            store.clear_occupancy(loser.id)
            lprio = EVENT_PRIORITY[loser.type]
            for pid in sorted(people_for(loser)):
                store.add_occupancy(pid, loser, lprio)
        else:
            # Not yet started: move it to begin after our window, then re-resolve.
            store.clear_occupancy(loser.id)
            loser.start_tick = end
            store.reschedule_event(loser)
            reserve(store, loser, now)
