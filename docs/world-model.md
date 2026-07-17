# A World Model for Evaluating PM Agents

*One simulated team-week, realistic where it counts — measurable where it
matters.*

The sim reproduces the slice of reality a PM operates in — a calendar, a board,
coworkers with habits, meetings where the truth lives — and gives an LLM agent
a real PM's moves: read the room, message people, keep the board honest. The
week is deterministic and its unmanaged outcome is pinned, so the agent's score
is a clean **lift over doing nothing**.

## What the world includes

- **A clock** — 1 tick = 1 simulated minute, 09:00–17:00, Mon–Fri (2400 ticks).
- **A Jira board** — epics/stories/tasks with priorities, estimates,
  dependencies, and a legal status workflow.
- **Coworkers** — NPCs who pick and work tickets per their persona, attend
  meetings, read Slack, take OOO and the occasional coffee break.
- **Meetings and transcripts** — standups, kickoffs, 1:1s; every meeting leaves
  a transcript when it ends, and informal `task` rows can track work the board
  has never heard of.
- **Slack with read-time semantics** — a message lands for everyone within the
  hour, but its *effects* fire only when an addressee reads it; reads defer
  past meetings and OOO.
- **Persistence** — one SQLite `world.db` per run plus an immutable `seed.db`
  snapshot, with a unified sim-time event log narrating everything.

## Why this is realistic

- **Real calendar time.** A meeting pauses whatever you were doing and the work
  resumes with its remaining minutes intact — interruptions cost exactly what
  they cost, never more. A meeting that collides with an OOO is *skipped*, not
  moved: a missed standup doesn't shift to the afternoon.
- **Honest capacity math.** Boards are tuned to real capacity: a 52 h board vs
  33.5 h of working time solo (once daily standups and a Tuesday OOO come off
  the calendar), or a zero-slack week per DRI on the team board — so every
  slip has a cause.
- **Coworkers with habits.** `perfect` closes work on finish, `heads_down`
  parks it in review until asked, `free_spirit` picks at seeded random —
  stalled handoffs aren't scripted, they emerge from the habits colliding.
- **Information conflict.** The Jira board and the meeting notes are allowed to
  disagree: in one week, 25 tasks live only in transcripts while the board
  stays empty all five days. The dashboard can lie; only a PM who reads the
  room sees the real project.
- **The PM steers, never builds.** The agent reads the board, Slack, calendar,
  and transcripts, then acts through communication and board hygiene:
  `send_slack` (naming a person closes their parked review; "please pick up
  KEY" preempts them onto that ticket), `create_jira_ticket` /
  `update_jira_status` to make the board reflect reality, and an append-only
  per-run `memory.md` — each transcript is readable once, on the day it
  appears, so takeaways must be banked in memory. It can never do the
  engineering work itself, only steer the people who do.
- **Persisted & replayable.** Every run is plain state, auditable with the
  stock sqlite3 CLI. Same seed, same week, minute for minute — a finished run
  can be inspected, diffed against its seed, and replayed.

## How the evaluation is done

Each scenario engineers one failure mode, pins its unmanaged baseline
(seed 42), and leaves headroom only the PM levers can recover. A do-nothing PM
lands exactly on the baseline.

**How the agent is scored:** the same seeded week runs with and without the PM,
and `pm eval` compares outcomes — `done/total` project tasks from ground truth
(the informal task table, board fallback), the PROJECT DONE verdict, and hours
of tickets closed. The agent's score is its *lift over the pinned baseline*;
messages, reads, and token spend earn nothing by themselves, and because the
week is deterministic, every point of lift is attributable to the agent's
messages — never to noise.

| Scenario (+`_with_agent`) | Engineered failure | Unmanaged | The PM fix |
|---|---|---|---|
| `single_engineer` | Random picks let 23 h of backlog displace the 29 h must-ship project. | 9/14 — project stranded | "please pick up KEY" |
| `two_engineers` | Parked reviews + dependency-blind picks stall zero-slack cross handoffs. | 8/16 — NOT DONE | Name clare to close reviews |
| `team_no_jira` | The whole project is tracked in meeting notes; the board never holds a ticket. | 15/25 — invisible on the board | Read the transcripts, file the tickets |

- **Mechanical grading** — `pm eval` scores completion from ground truth (notes
  table, board fallback) plus hours closed; no LLM judge.
- **Cost beside the score** — every model call and tool call is token-logged to
  `agent-<model>.jsonl` and summed into the report, next to the outcome — never
  blended into it.
- **Hermetic tests** — scripted fake clients pin the do-nothing baseline; real
  models slot in via one env var.
- **Auditable** — the event log, transcripts, and HTML viz replay *why* the
  week ended as it did (`eval.json` per run).

Verified per-scenario outcomes are cataloged in `pm/scenarios/scenarios.md`.

## Challenges encountered

Practical problems hit while building and running the LLM-PM sim, and what we
did about them.

### 1. `openai/gpt-5.5-pro` round-trips are super slow

A single review round-trip through OpenRouter took 60s+ with
`openai/gpt-5.5-pro` — a `pm sim` week blocks on 2-3 round-trips per review,
~30 reviews a week, so runs were unusably slow. Switched the default model to
`anthropic/claude-opus-4.8` (~8.6s per round-trip; see the wall-clock
`llm_request`/`llm_call` stamps in the run logs, and `examples/run_agent_llm.py`
for measuring a model standalone).

### 2. The stateless agent repeats itself: re-reads transcripts, re-creates tickets

Each review loop starts a fresh `LLMAgent` with no memory of earlier reviews.
Two symptoms: the agent re-read the same transcripts review after review (the
same kickoff body fetched every 4 hours, visible as repeated
`agent.read_transcript` lines for one source path), and — worse — it repeatedly
created the same Jira tickets, filing duplicates for action items it had
already filed in an earlier review, since nothing told it the work was done.

Fix: give the agent a persistent memory and make re-reading impossible.

- `append_memory` writes notes to the run's `memory.md` (append-only, stamped
  with the sim tick); the file is injected into every review's prompt, so
  transcript takeaways, decisions, and filed-ticket keys survive across
  reviews without re-reading the sources.
- Each transcript may be read **once per run**, and only on the sim-day it
  became available; later attempts return an error payload pointing the model
  at its memory instead. The `read_transcripts` index flags already-`read`
  entries and returns cheap previews, not bodies.

### 3. Filed tickets drop the duration from the meeting notes

In the no-Jira scenarios the meeting notes carry an Estimate column per task,
but when the agent filed the missing work with `create_jira_ticket` it passed
only a title and assignee — `estimate_minutes` silently fell back to the tool
default (60). Every filed ticket landed as 1h regardless of its real size, and
since the eval sums `estimate_minutes` over done Jira tasks, the closed-hours
metric under-counted the recovered work.

Fix: spell it out at both layers. The tool schema now documents the fallback
("Estimated work, in minutes (default 60)"), and the scenario prompt requires
carrying over the title, assignee, **and** `estimate_minutes` from the notes'
Estimate column — every ticket the agent files must have its estimate set.

## Scope, honestly

One week of execution dynamics — picks, handoffs, reviews, visibility. No
scope negotiation or estimation error, by design: it isolates the
read-the-room-then-nudge loop a PM agent must master.

---

Companion to [`architecture.md`](architecture.md) (rendered:
[`architecture.html`](architecture.html)); this document is rendered as
[`world-model.html`](world-model.html).
