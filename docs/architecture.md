# Architecture

How the Project Manager Simulation is put together, and the systems decisions it
turns on. For the quick tour (layout, schema map), see the
[README](../README.md#architecture-at-a-glance); this is the deeper reference.

## Layers

The package is a small stack; arrows point at dependencies (lower layers know
nothing of higher ones):

```
env      facade: one handle over a run (owns store + sim kernel)
  ├─ sim    dynamics: clock, scheduler, engine, durative events, calendar, activities
  ├─ jira   Jira-style issue tracker over the Store (its own issue tables)
  ├─ npc    coworker roster + persona + board-pickup scheduling hooks
  └─ db     persistence: sqlite connection, schema.sql, the typed Store
        └─ world   domain layer: Pydantic entities (models.py) + read view (resources.py)
```

`world` depends on nothing; `db` maps rows ↔ `world` models; `sim`/`jira`/`npc`
act through `db`; `env` assembles them.

## How simulated time advances

Time is an integer **tick** — one tick = one simulated minute — stored in
`meta.current_tick`. It is part of the single source of truth (survives
save/resume) and is **decoupled from wall-clock**: an agent action may cost many
ticks regardless of how long inference took.

`SimClock` (`pm/sim/clock.py`) models a **work-hours calendar**: 09:00–17:00 on
weekdays, 480 ticks/day, **2400 ticks/week** (tick 0 = Mon 09:00, 2400 = Fri
17:00). Nights don't exist — 17:00 rolls straight into the next 09:00, so a delay
that crosses 5pm lands the next morning.

The engine advances **one minute at a time**:

- `Engine.step()` bumps the clock by 1 and updates the queue for that minute.
- `Engine.advance(n)` / `advance_to(tick)` loop `step()`.
- `Simulation.run(on_tick=…)` (`pm/sim/simulation.py`) drives a whole Mon 09:00 →
  Fri 17:00 week, calling an optional per-minute hook before each step.

The **sync/async seam** is `Engine.perform_action(actor, cost, effect)`: the
effect (an agent's tool mutation) runs at the *current* tick with no time passing,
then `advance(cost)` lets background events start/complete during that window.
Nothing runs on a real thread — the week is driven entirely by the clock. (See
[sync-vs-async.md](sync-vs-async.md).)

## How world state is stored

**SQLite is the single source of truth.** Each run is one `runs/<id>/world.db`,
plus an immutable `runs/<id>/seed.db` snapshot taken at `Env.make` — the basis for
reproducible replays (`Env.reset`) and diffable grading (seed → current).

All access goes through **`Store`** (`pm/db/store.py`), the only place raw SQL
lives. It maps `sqlite3.Row` objects ↔ the Pydantic entities in
`pm/world/models.py`, and during a run the engine is the **sole writer**. The DDL
is `pm/db/schema.sql`; the Jira board adds its own `issue` / `issue_dependency`
tables via `pm/jira/repository.py` (idempotent `CREATE TABLE IF NOT EXISTS`, no
edit to the core schema). Because state is plain SQLite plus an append-only
`event_log` trace, any run is auditable with the stock `sqlite3` CLI.

## Operator surface (scenario-keyed)

The CLI (`pm/cli.py`) is deliberately narrow: `sim`, `eval`, and `viz` each take a
single `--scenario`, and **the scenario name is the run id and the output folder**.
There is no persona or run-id flag — **each scenario module bakes in its own
personas** (`build(member_persona=…)` defaults, applied via
`pm/npc/cast.py::with_personas`), so `pm sim --scenario X` alone reproduces that
scenario's documented outcome. One scenario → one run.

Everything a scenario produces lives under **`runs/<scenario>/`**, the self-contained
result bundle:

- `world.db`, `seed.db` — from `sim` (the finished world + its immutable seed).
- `eval.json` — from `eval` (`to_dict(report)`, written alongside the printed report).
- `calendars.html`, `jira_tasks.html` — from `viz` (static HTML/SVG, no JS/browser).

`pm/scenarios/runner.py::drive` is the shared driver used by both `pm sim` and the
catalog test: it composes each scenario's member pickup hook with an optional PM
review hook (`agent_review_hook`, run first so a same-tick close/directive lands before
the person it steers picks), runs the week, and fires a final PM close-out. The registered
scenarios and their verified outcomes are cataloged in
[`pm/scenarios/scenarios.md`](../pm/scenarios/scenarios.md).

## How events are scheduled

Every asynchronous activity is one durative **`Event`** (`pm/sim/events.py`) with a
uniform lifecycle:

```
pending ──start()──▶ active ──done()──▶ done        (+ cancelled)
```

There is exactly **one row per event** in the `event` table, and that table *is*
the queue — indexed `(status, start_tick, seq)`, with **no parallel in-memory
heap**. `Scheduler.schedule(event)` (`pm/sim/scheduler.py`) stamps a monotonic
`seq` (resumed past the persisted max so it's never reused) and `created_tick`,
then persists via `Store.upsert_event`. Global order is therefore
`(start_tick, seq)` — deterministic across replays and resumes.

Dispatch is purely clock-driven: each `Engine.step()` **starts** every pending
event whose `start_tick` has arrived and **completes** every active event whose
duration has elapsed. Behaviour lives on the `Event` subclass (`_on_start` /
`_on_done`) — the engine just moves rows through `start() → done()`; the
subclasses apply the world mutation through `engine.store`. Instantaneous events
have `duration == 0` and start and finish in the same tick.

## Contention & interruption

An NPC can only do one thing at a time, and a meeting must win over routine work.
Two cooperating mechanisms enforce this; both express the same rule — **higher
priority preempts, and preempted work pauses and resumes** — at different moments.

### Calendar reservation (event level, at schedule time)

`pm/sim/calendar.py::reserve` is called from `Scheduler.schedule` for *occupying*
(durative) event types. Priority is by type — a meeting (`MEETING`, 100)
outranks work (`JIRA_TICKET`, 20). The `occupancy` table is
the materialized per-NPC calendar of `[start, end)` blocks. On reserve:

- **Yield:** a new block is pushed to start after any equal-or-higher block it
  overlaps for its people.
- **Bump:** it then displaces every lower-priority block it overlaps. A bumped
  event that is **not yet started** is moved to begin after the new window; one
  that is **already active pauses and resumes** — its `duration` is extended by the
  meeting's length, so it finishes later having "lost" the meeting's minutes.

Because this all happens at reserve time, the engine then just runs events on the
resolved schedule.

### Activity manager (runtime, per NPC)

`pm/sim/activity.py::ActivityManager` is the richer, runtime model of *doing*. An
**Activity** (meeting, jira_work, write_doc, review_doc, coffee_break, …) runs with
a state machine:

```
backlogged → started → (interrupted ↔ started) → done      (+ cancelled)
```

The manager enforces **one `started` activity per attender**. Each tick it burns
down started activities and re-dispatches. When a higher-priority activity claims
an attender who is mid-work, the lower-priority one is **interrupted** — its
`remaining` is frozen — and it **resumes** (re-`started`) once the attender is free
again. So a coworker heads-down on a ticket, pulled into a meeting, pauses the
ticket and picks it back up afterward with exactly the work that was left. If an
attender is already in an equal-or-higher activity, the incoming one waits
(`backlogged`). Priority is the only per-kind knob (plus the `on_start`/`on_done`
effect hooks).
