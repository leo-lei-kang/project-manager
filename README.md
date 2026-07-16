# Project Manager Simulation Environment

A single-node, deterministic simulation of a project manager's first week at a
SaaS company. Simulated time is decoupled from wall-clock inference latency,
coworkers act asynchronously, and outcomes are graded defensibly.

Built in layers on a SQLite single source of truth: the persistence layer + typed
repository, the **simulation kernel** (clock + durative-event scheduler + engine +
the bounded work-week `Simulation`), a **Jira-style issue tracker**, **stateful NPC
coworkers** who work the board over sim-time, the `Env` facade, an operator CLI, and
an **agent tool surface** (chat / board / calendar) exposed over MCP with an
OpenRouter-driven agent. See [Agent](#agent) and [Roadmap](#roadmap).

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
  `event_log`, you can audit any run with the stock `sqlite3` CLI.

```
pm/
  db/           # SQLite persistence: database.py, schema.sql, store.py (only SQL boundary)
  world/        # domain layer: models.py (Pydantic entities), resources.py (read view)
  sim/          # dynamics: clock, scheduler, engine, events (durative), simulation, calendar
  jira/         # Jira-style issue tracker (its own issue tables) over the Store
  npc/          # cast (members/stakeholders/agent), board-pickup hooks, per-event reactions
  agent/        # the agent's tool surface: AgentTools + MCP server + OpenRouter driver
  env/          # Env facade over store + sim kernel (make/load/reset/db)
  eval/         # deterministic evaluation over a run's final board state
  viz/          # static-HTML renderers: per-person calendars, ticket week-timeline
  scenarios/    # code-seeded scenarios (team_week, tight_week) + generator
  cli.py        # `pm sim` / `pm eval` / `pm viz`
examples/       # runnable demos (see examples/README.md)
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
| Work | `task`, `task_dependency` | tasks with blocked state and dependency edges |
| Chat | `channel`, `channel_member`, `message` | DMs and channels |
| Email | `email_thread`, `email`, `email_recipient` | slower-cadence threads |
| Calendar | `meeting`, `meeting_attendee`, `transcript` | meetings + attend-only transcripts |
| Docs | `document` | discoverable specs/PRDs/reports |
| Jira | `issue`, `issue_dependency` | issue tracker (added by `pm/jira`, own DDL) |

## Setup

Requires [`uv`](https://docs.astral.sh/uv/) and Python 3.11+ (uv will fetch the
interpreter if needed). No database server and no API key are required for the
core simulation.

```bash
uv sync                 # core simulation — no LLM or API key required
uv sync --extra mcp     # + serve the agent's tools over MCP (`pm-mcp`)
uv sync --extra agent   # + drive the agent with an OpenRouter model (`pm-agent`)
```

## Usage

```bash
# Build a scenario run and simulate its work week. The run id defaults to the
# scenario name, so this writes runs/tight_week/{world.db, seed.db} and runs it.
uv run pm sim --scenario tight_week

# Evaluate the outcome.
uv run pm eval --run-id tight_week

# Render the ticket week-timeline + per-person calendars to static HTML:
# writes runs/tight_week/{calendars,jira_tasks}.html.
uv run pm viz --run-id tight_week

# Inspect the database directly.
uv run sqlite3 runs/tight_week/world.db '.tables'
uv run sqlite3 runs/tight_week/world.db 'SELECT * FROM meta;'
```

## Running the simulation

The quickest way is `pm sim` — build a scenario run and simulate its work week
(Mon 09:00 → Fri 17:00, NPC coworkers working the board) in one command:

```bash
uv run pm sim --scenario tight_week                    # build + simulate -> runs/tight_week/
uv run pm sim --run-id tight_week                      # continue an existing run
uv run pm sim --scenario tight_week --run-id myweek    # same, under a custom run id
# -> Simulated Mon 09:00 -> Fri 17:00 (tick 2400); 154 event transitions fired.
```

Scenarios: `tight_week` (a capacity-saturated board the team barely finishes) and
`team_week` (a lighter board). The run id defaults to the scenario name; the run
persists to `runs/<run-id>/`, ready for `pm eval`.

For a per-day progress report, the example script does the same with narration
and persists to `runs/tight_week/`:

```bash
uv run python examples/run_tight_week.py
# -> per-day progress report, then:
#    runs/tight_week/seed.db   (immutable seeded starting state)
#    runs/tight_week/world.db  (the finished week, ready to evaluate)
```

The same loop in code, for any scenario:

```python
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.behavior import assignee_pickup_hook
from pm.scenarios import tight_week          # or team_week
from pm.sim.simulation import Simulation

env = tight_week.build()                     # seed cast + board + meetings
api = JiraApi(JiraRepository(env.store), env.engine)
Simulation(env).run(on_tick=assignee_pickup_hook(api, tight_week.MEMBERS,
                                                 tight_week.PROJECT_ID))
env.close()
```

Each tick the hook lets idle coworkers pick up their next ready issue (by
priority, respecting dependencies — or per their seeded behavior persona), and
the engine fires the pre-booked meetings on the clock.

## Running the evaluation

`pm eval` reads a run's `world.db` Jira results and reports the hours of task
work completed, whether the week goal was accomplished, who accomplished what,
and what remains. The goal is **accomplished** when every task in the project is
done and the last completion lands at or before the project's deadline:

```bash
uv run pm eval --run-id tight_week           # human-readable report
uv run pm eval --run-id tight_week --json    # the same report as JSON
uv run pm eval --run-id demo --project GA    # pick a project explicitly
```

```
Project GA — Live Transcription GA
Goal: ACCOMPLISHED — 66/66 tasks done, 176.25h of 176.25h completed
  last completion Fri 17:00 (tick 2400); deadline Fri 17:00 (tick 2400) — met
By person:
  Alice    16 done (33.75h), 0 remaining (0h)
      done GA-3     Audit GA release checklist        at Mon 11:00
      ...
Remaining:
  none
```

In code, the same evaluation is `pm.eval`'s pure functions over any `Store`:

```python
from pm.db.store import Store
from pm.eval import evaluate, format_report

store = Store.open("runs/tight_week/world.db")
print(format_report(evaluate(store)))        # or evaluate(store).goal_accomplished
store.close()
```

## Examples

Self-contained, runnable demos live in [`examples/`](examples/) (details in
[`examples/README.md`](examples/README.md)). Each builds a throwaway database,
prints what it does, and cleans up after itself:

```bash
uv run python examples/jira_board.py       # the pm.jira board (static)
uv run python examples/run_week.py         # the full Mon->Fri sim loop
uv run python examples/npc_calendar.py     # per-NPC calendar: meetings vs. work
uv run python examples/run_tight_week.py   # capacity-saturated week (persists to runs/)
```

The exception: `run_tight_week.py` deliberately persists its run to
`runs/tight_week/` so the result can be inspected and evaluated (see
[Running the evaluation](#running-the-evaluation)).

| Example | Shows |
|---------|-------|
| `jira_board.py` | Realistic one-week transcription sprint (`pm.jira`): 1 epic, 5 stories, 25 tasks, estimates, owners, rollups, cross-story dependencies; cascade unblocking. |
| `run_week.py` | The bounded Mon 09:00 → Fri 17:00 `Simulation` loop over the `team_week` scenario: the engineer cast works the assigned `TRANS` Jira board while meetings fire, with a per-day report of events and the board's end-of-day status. |
| `npc_calendar.py` | Coworkers' week on the per-NPC calendar — meetings vs. work, with priority bump/defer and pause/resume. |
| `run_tight_week.py` | The `tight_week` scenario: a capacity-saturated board (66 tasks tiling every member's meeting-free calendar exactly) worked in dependency + priority order — the last task completes at exactly Fri 17:00, tick 2400 of 2400. Persists `runs/tight_week/{seed.db, world.db}`. |

## Agent

The agent-under-test acts through a small, explicit tool surface in
[`pm/agent/`](pm/agent) — `AgentTools`, bound to a run:

| Tool | Kind | Does |
|------|------|------|
| `send_slack(channel_id, body)` | action | posts a message; routes through `Engine.perform_action` so it consumes sim-time and is logged |
| `read_slack(channel_id)` | read | the messages in a channel |
| `read_jira_board(project_id)` | read | the board's issues + a status breakdown |
| `read_calendar(person_id=None)` | read | the meetings a person attends (default: the agent) |

Reads are free; only `send_slack` advances the clock. Following the
[fleet-sdk](../fleet-sdk) pattern, these are exposed over **MCP** (name = function,
description = docstring, schema = the typed signature) and driven by an
**OpenRouter**-hosted model.

```bash
# 1) Create a run for the agent to act on: seed a scenario WITHOUT simulating
#    the week (the module's __main__ builds runs/team_week/ and stops).
uv run python -m pm.scenarios.team_week

# 2) Serve the tools over MCP (binds the run named by PM_RUN_ID; defaults to "demo").
uv sync --extra mcp
PM_RUN_ID=team_week uv run pm-mcp   # streamable-http on PM_MCP_HOST/PM_MCP_PORT

# 3) Drive the agent with an OpenRouter model. Copy .env.example -> .env and set
#    OPENROUTER_API_KEY + OPENROUTER_MODEL (any OpenRouter model id, configurable).
uv sync --extra agent
uv run pm-agent "Review the board and post a status update in #eng"
```

`.env` (gitignored) holds `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, and optional
`PM_MCP_URL` — see [`.env.example`](.env.example).

### Two ways to consume the server

Following fleet-sdk, tool execution can happen on either side:

- **Local loop** (default) — we run `model → tool call → result` ourselves. Works
  with any OpenAI-compatible endpoint (OpenRouter) and a localhost server. This is
  `pm-agent` / `LLMAgent` + `McpBackend`.
- **Remote (provider-side)** — hand the server URL to a provider that runs MCP
  itself (OpenAI Responses API, Anthropic MCP connector); it executes the tools, no
  loop of ours. `RemoteMCP` (mirrors fleet's `SyncMCPResource`) produces the
  descriptors; needs a URL the provider can reach (public/tunnelled, not localhost):

  ```python
  from pm.agent import remote_mcp
  res = remote_mcp("https://my-host/mcp")   # or $PM_MCP_URL
  res.openai()      # -> {"type": "mcp", "server_label": ..., "server_url": ..., ...}
  res.anthropic()   # -> {"type": "url", "url": ..., "name": ...}
  await res.list_tools()                     # live tool list (MCP handshake)
  ```

See both, self-contained (each spins up the server on a free port and tears it down):

```bash
uv run --extra agent python examples/run_agent_llm.py --mcp     # local loop over a real server
uv run --extra agent python examples/run_agent_llm.py --remote  # remote descriptors + live tools
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
surface, the **agent tool surface over MCP + OpenRouter driver** (see
[Agent](#agent)), and the first slice of the **evaluator** (`pm/eval` +
`pm eval` — a deterministic report over the final board state; see
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
# project-manager
