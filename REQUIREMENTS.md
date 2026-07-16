# Requirements

Distilled from `problem-statement.md`. Each line is a checkable requirement for
the Python project.

## Objective

Build a single-node, deterministic simulation of a project manager's first week
at a SaaS company, where simulated time is decoupled from wall-clock inference
latency and the agent's actions are graded defensibly.

## Functional

- **Simulated time** advances in explicit ticks, decoupled from inference latency.
- **World model** is persistent, with ≥1 active project containing task
  dependencies, blockers, and stakeholder pressure.
- **Tool surfaces**: chat, email, calendar, meeting/transcript capture, task
  tracking, and document management.
- **Coworkers**: multiple stateful NPCs with distinct roles, proactive outreach,
  and realistic response delays.
- **Evaluation** rewards improved outcomes and sound decisions over superficial
  activity.
- **Legible systems boundaries**: what advances synchronously with an agent
  action vs asynchronously in the background; how scenario state is owned and
  mutated; how new scenarios are added without per-scenario code ("no prompt
  spaghetti").
- **Model-based verification (if used)** documents why it is needed, what inputs
  it sees, and how results are kept stable enough to trust.

## Non-functional / constraints

- Single repository; clone → start locally → drive the main flows via documented
  commands.
- Single-node (not distributed); runs must be deterministic and replayable.
- Python ≥ 3.11.

## Deliverables

- The full runnable system.
- ≥1 fully-authored PM scenario: seeded company state, tool data, coworker
  personas, and evaluation ground truth.
- `README.md` covering setup, how to start it, how to drive the main flows, and
  how to run the evaluation.
- Grading detail sufficient to inspect score components and example outcomes and
  to see how the evaluator resists reward hacking.
- Documentation of the architecture and realism choices.
