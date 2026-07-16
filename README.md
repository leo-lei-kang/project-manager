# Project Manager Simulation Environment

A single-node, deterministic simulation of a project manager's first week at a
SaaS company. Simulated time is decoupled from wall-clock inference latency,
coworkers act asynchronously, and outcomes are graded defensibly.

Built in layers on a SQLite single source of truth: a **simulation kernel**, a
**Jira-style issue tracker**, **stateful NPC coworkers** who work the board over
sim-time, an operator CLI, and an **agent tool surface** driven in-process by an
OpenRouter-hosted model — see [Architecture](#architecture-at-a-glance),
[Agent](#agent), and [Roadmap](#roadmap).

## Architecture at a glance

- **SQLite is the single source of truth.** The whole company/project world
  lives in one SQLite file per run (`runs/<run_id>/world.db`), alongside an
  immutable `seed.db` snapshot taken at creation for reproducible, diffable
  grading. NPCs and the Jira board are views and commands over it — never a
  parallel in-memory copy.
- **One writer.** All mutation goes through `Store` (`pm/db/store.py`); no
  raw SQL leaks past it. During a run the engine is the only caller of the
  mutating methods.
- **Sync vs async is explicit.** An agent action resolves synchronously against
  the current tick (`Engine.perform_action`), then time advances by the action's
  cost and the engine starts/completes every durative event that comes due
  (`Engine.advance` / `Simulation.run`). Nothing runs in a real background thread
  — the week is driven entirely by the clock. The persisted `event` table *is* the
  queue; there is no parallel in-memory heap.
- **Deterministic.** Every time column is an integer **sim tick** (one tick = one
  simulated minute), not wall-clock. A seeded RNG (stored in `meta`) will drive
  NPC delays, so a scenario replays identically.
- **Inspectable.** Because state is plain SQLite plus an append-only
  `event_log`, you can audit any run with the stock `sqlite3` CLI. `pm sim`
  narrates that log to the console live (silence with `--no-verbose`) and
  `pm log --scenario <name>` replays a finished run's sim-time timeline —
  event lifecycles, NPC decisions and activity transitions, and the LLM
  agent's calls.

```
pm/
  db/           # SQLite persistence: database.py, schema.sql, store.py (only SQL boundary)
  world/        # domain layer: models.py (Pydantic entities), resources.py (read view)
  sim/          # dynamics: clock, scheduler, engine, events (durative), simulation, calendar
  jira/         # Jira-style issue tracker (its own issue tables) over the Store
  npc/          # cast (members/stakeholders/agent), board-pickup hooks, per-event reactions
  agent/        # the agent's tool surface: AgentTools + in-process OpenRouter driver
  env/          # Env facade over store + sim kernel (make/load/reset/db)
  eval/         # deterministic evaluation: project completion + closed Jira hours
  viz/          # static-HTML renderers: per-person calendars, ticket week-timeline
  scenarios/    # code-seeded scenarios (team_no_jira, test_two_engineers_mixed, test_single_engineer_free_spirit) + generator
  cli.py        # `pm sim` / `pm eval` / `pm viz`
examples/       # runnable demos (see Examples below)
tests/          # pytest suite covering db, sim, jira, npc, agent, env, scenarios
```

For the core mechanics — **how simulated time advances, how world state is
stored, how events are scheduled**, and how meetings pause and resume in-progress
work — see [`docs/architecture.md`](docs/architecture.md).

### Schema map

| Group | Tables | Purpose |
|-------|--------|---------|
| Sim machinery | `meta`, `event`, `event_log` | clock/config, persisted durative-event queue, append-only trace |
| Org | `person`, `project` | coworkers + the agent-under-test; the active project |
| Chat | `channel`, `channel_member`, `message` | DMs and channels |
| Email | `email_thread`, `email`, `email_recipient` | slower-cadence threads |
| Calendar | `occupancy`, `meeting`, `meeting_attendee`, `transcript` | per-NPC time blocks; meetings + attend-only transcripts |
| Docs | `document` | discoverable specs/PRDs/reports |
| Jira | `issue`, `issue_dependency` | issue tracker (added by `pm/jira`, own DDL) |

## Setup

Requires [`uv`](https://docs.astral.sh/uv/) and Python 3.11+ (uv will fetch the
interpreter if needed). No database server and no API key are required for the
core simulation.

```bash
uv sync                 # core simulation — no LLM or API key required
```

## Usage

Every command takes a single `--scenario`, whose name is the run id and output
folder — all results for a scenario live under `runs/<scenario>/`.

```bash
# Build a scenario run and simulate its work week -> runs/test_two_engineers_mixed/{world.db, seed.db}.
uv run pm sim --scenario test_two_engineers_mixed

# Evaluate the outcome (also written to runs/test_two_engineers_mixed/eval.json).
uv run pm eval --scenario test_two_engineers_mixed

# Render the ticket week-timeline + per-person calendars to static HTML:
# writes runs/test_two_engineers_mixed/{calendars,jira_tasks}.html.
uv run pm viz --scenario test_two_engineers_mixed

# Print the run's sim-time timeline (events, NPC decisions, agent calls).
uv run pm log --scenario test_two_engineers_mixed

# Inspect the database directly.
uv run sqlite3 runs/test_two_engineers_mixed/world.db '.tables'
uv run sqlite3 runs/test_two_engineers_mixed/world.db 'SELECT * FROM meta;'
```

## Running the simulation

The quickest way is `pm sim` — build a scenario run and simulate its work week
(Mon 09:00 → Fri 17:00, NPC coworkers working the board) in one command:

```bash
uv run pm sim --scenario test_two_engineers_mixed    # a mixed-persona board that misses the week
uv run pm sim --scenario team_no_jira                # a week tracked only in meeting notes
# -> prints the simulated span (Mon 09:00 -> Fri 17:00) and the events fired.
```

Re-running a scenario continues an unfinished run in place. The scenario is the only
input — there is no persona flag; **each scenario bakes in its own personas**.

| Board | Stresses |
|----------|----------|
| `test_two_engineers_mixed` | two engineers splitting the transcripts project; tickets cross-block just-in-time |
| `test_single_engineer_free_spirit` | one engineer, the 40-h transcripts project + 20 h of backlog — triage decides if it ships |
| `team_no_jira` | the five-member team's week tracked only in meeting notes; the Jira board stays empty |

Each board ships as self-contained scenarios — a baseline persona and a
misbehaving-persona variant. The verified persona configurations (each runnable as
`pm sim --scenario <name>`) are cataloged with their outcomes in
[`pm/scenarios/scenarios.md`](pm/scenarios/scenarios.md).

The same loop in code, for any scenario:

```python
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.behavior import assignee_pickup_hook
from pm.scenarios import test_two_engineers_mixed   # or test_single_engineer_free_spirit
from pm.sim.simulation import Simulation

env = test_two_engineers_mixed.build()              # seed cast + board
api = JiraApi(JiraRepository(env.store), env.engine)
Simulation(env).run(on_tick=assignee_pickup_hook(api, test_two_engineers_mixed.MEMBERS,
                                                 test_two_engineers_mixed.PROJECT_ID))
env.close()
```

Each tick the hook lets idle coworkers pick up their next ready issue (by
priority, respecting dependencies — or per their seeded behavior persona), and
the engine fires the pre-booked meetings on the clock.

## Running the evaluation

`pm eval` reads a run's `world.db` and grades whether the high-priority project
is done, who accomplished what, and what remains. The project's task list comes
from the informal `task` table (the meeting-notes breakdown, including work that
was never filed as a Jira ticket); a run with no informal tasks falls back to
the Jira board's tasks. The goal is **accomplished** when every project task is
done — there is no deadline check. Separately, the report sums the hours of
Jira tickets closed:

```bash
uv run pm eval --scenario test_two_engineers_mixed          # human-readable report (+ runs/test_two_engineers_mixed/eval.json)
uv run pm eval --scenario test_two_engineers_mixed --json   # the same report as JSON on stdout
uv run pm eval --scenario test_two_engineers_mixed --project MT   # pick a project explicitly
```

```
Project MT — Meeting Transcripts v1
Goal: PROJECT NOT DONE — 8/16 tasks done (source: board)
Jira tickets closed: 40h of 80h
By person:
  ...per-person done/remaining breakdown, one line per task...
Remaining:
  ...one line per unfinished task...
```

In code, the same evaluation is `pm.eval`'s pure functions over any `Store`:

```python
from pm.db.store import Store
from pm.eval import evaluate, format_report

store = Store.open("runs/test_two_engineers_mixed/world.db")
print(format_report(evaluate(store)))        # or evaluate(store).goal_accomplished
store.close()
```

## Examples

Self-contained, runnable demos live in [`examples/`](examples/); each module's
docstring says what it shows. They build a throwaway database, print what they
do, and clean up after themselves — for example:

```bash
uv run python examples/run_week.py   # the full Mon->Fri sim loop, per-day report
```

## Agent

The agent-under-test acts through a small, explicit tool surface in
[`pm/agent/`](pm/agent) — `AgentTools`, bound to a run:

| Tool | Kind | Does |
|------|------|------|
| `send_slack(channel_id, body)` | action | posts a message; routes through `Engine.perform_action` so it consumes sim-time and is logged |
| `read_slack(channel_id)` | read | the messages in a channel |
| `read_jira_board(project_id)` | read | the board's issues + a status breakdown |
| `read_calendar(person_id=None)` | read | the meetings a person attends (default: the agent) |
| `read_transcripts()` | read | the markdown transcripts of meetings that have ended — status, open questions, decisions waiting on the PM |

Reads are free; only `send_slack` advances the clock. The tools are handed to an
**OpenRouter**-hosted model as function schemas and driven **in-process** — a
`model → tool call → result` loop in one process, no server or transport
(`LLMAgent` + `InProcessBackend`). Inside a simulated week, the agent runs as
the scenario runner's review hook (`pm/scenarios/runner.py`):
`pm sim --scenario test_single_engineer_with_agent` has the LLM PM review the
board every four sim-hours (needs `OPENROUTER_API_KEY` in `.env`; model from
`OPENROUTER_MODEL`). Every model round-trip and tool call is logged with token
usage to `runs/<scenario>/agent-<model>.jsonl` (one file per driving model) and
mirrored into the `event_log` timeline — `pm eval` sums the input/output tokens
and `pm viz` plots when the agent acted (`agent_activity.html`).

To exercise the LLM loop against a seeded throwaway board, copy
[`.env.example`](.env.example) to `.env` (gitignored) and set
`OPENROUTER_API_KEY`, then:

```bash
uv run python examples/run_agent_llm.py           # in-process loop
uv run python examples/run_agent_llm.py --list    # available model ids
```

## Tests

```bash
uv run pytest
```

Covers: schema + persistence round-trips and foreign-key enforcement, the
durative-event lifecycle and clock stepping, the per-NPC calendar (priority
bump/defer, pause & resume), activities (interrupt/resume), the `pm.jira` board
(hierarchy, rollups, derived blocked state), the NPC cast + board pickup, the agent
tools, and the `Env` run lifecycle.

## Roadmap

Built so far: the persistence + sim kernel, the `pm.jira` board, `pm.npc`
coworkers that pick up and work issues over the week, the `Env`/CLI operator
surface, the **agent tool surface + in-process OpenRouter driver** (see
[Agent](#agent)), and the first slice of the **evaluator** (`pm/eval` +
`pm eval` — a deterministic project-completion report over a run's final state; see
[Running the evaluation](#running-the-evaluation)). Scenario state is seeded in
code (`pm/npc/cast.py`, `pm/scenarios/`, the `examples/` builders) rather than a
data loader.

Not yet attached (designed to plug in without schema changes):

1. **Evaluator depth** — extend the board report with an `event_log` rubric
   (process quality, not just outcomes) and an optional bounded LLM judge for
   fuzzy checks.
2. **Data-driven scenario authoring** — a loader so new scenarios need no new code.
3. **More agent tools** — email and docs surfaces alongside the current
   chat / board / calendar tools.
