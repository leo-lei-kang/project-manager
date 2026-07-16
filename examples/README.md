# Examples

Self-contained, runnable demonstrations of the simulation. Each script builds a
throwaway database, prints what it does, and cleans up after itself. Run any of
them with `uv run`:

```bash
uv run python examples/jira_board_example.py       # the pm.jira board (static)
uv run python examples/jira_board_example_html.py  # ...rendered to HTML
uv run python examples/npc_board.py                # NPCs work the board over a week
uv run python examples/run_week.py                 # the full Mon->Fri sim loop
uv run python examples/run_tight_week.py           # the capacity-saturated week (persists to runs/)
uv run python examples/visualize_calendars.py <run_id>   # per-person calendars -> HTML
uv run python examples/visualize_jira_tasks.py <run_id>  # ticket week-timeline -> HTML
```

Exceptions to the throwaway rule: `run_tight_week.py` persists its run to
`runs/tight_week/{seed.db, world.db}` so the seeded and finished states can be
inspected afterwards, and the two `visualize_*.py` wrappers write their HTML
into an existing `runs/<run_id>/` (they are thin argparse fronts over
[`pm.viz`](../pm/viz) — the same renderers behind `uv run pm viz`).

| Example | Shows |
|---------|-------|
| `jira_board_example.py` | The `pm.jira` vertical slice: one epic → 5 stories → 25 tasks with hour estimates, owners, rollups, and cross-story `blocks` dependencies; how finishing a blocker cascades to unblock its dependents. |
| `jira_board_example_html.py` | Renders that same board to a static HTML issue tree + dependency graph (reuses `seed_world`/`build_board`). |
| `npc_board.py` | NPC coworkers (Alice/Bob/Clare/David/Elieen) autonomously take and work their assigned, ready issues to done over the simulated work week via `JiraTicketEvent`; dependencies unblock downstream work as blockers finish. |
| `run_week.py` | The bounded Mon 09:00 → Fri 17:00 `Simulation` loop over the `team_week` scenario: the engineer cast works the assigned `TRANS` Jira board (`assignee_pickup_hook`) while meetings fire on the clock, printing a per-day report of the events that happened and the board's end-of-day issue states. |
| `run_tight_week.py` | The `tight_week` scenario: a capacity-saturated `GA` board (66 tasks tiling every member's meeting-free calendar exactly) worked in dependency + priority order — the last task completes at exactly Fri 17:00, tick 2400 of 2400. Persists `runs/tight_week/{seed.db, world.db}` for inspection. |
| `visualize_calendars.py` | One Mon–Fri 09:00–17:00 grid per person from a run's `world.db` — meeting / Jira-work / OOO blocks at 1px per minute (`pm.viz.write_calendars`). |
| `visualize_jira_tasks.py` | The run's tickets on the 5-day × 8-hour week timeline as inline SVG (one lane per person, bars at actually-worked intervals, dependency arrows) plus a deterministic topological completion order (`pm.viz.write_jira_tasks`). |

## What these use

The examples exercise the implemented surfaces: `Env`, `Store`, the sim kernel
(`SimClock` / `Scheduler` / `Engine` / durative `Event`s), the `Simulation` loop,
`pm.jira`, and `pm.npc` (roster + pickup hooks). No LLM or API key is required.
