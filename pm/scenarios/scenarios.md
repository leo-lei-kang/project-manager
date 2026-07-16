# Scenario catalog — personas × boards

Verified configurations built from two Jira boards, one notes-only board, and
three behavior personas. Every row below was actually run; the outcome column
is the observed `pm eval` result (seed 42).

**Boards** (each `pm/scenarios/<name>.py`):

- `single_engineer` — alice alone carries the whole Meeting
  Transcripts v1 push (`pm/transcript/project_single_engineer.md`), split into
  two epics: a high-priority one with nine 3-5 h project tasks (32 h — it
  *can* ship in full around the daily 30-min standups, 37.5 h of working time)
  and a low-priority backlog epic with five tickets (20 h) the week cannot
  also absorb. The verdict
  is PROJECT NOT DONE by design (55 h board); the success criterion is "the
  whole 35-h project ships", with whatever is left listed under the eval's
  Remaining section. Personas default to `free_spirit` (backlog picks displace
  project work); `build(member_persona=PERFECT)` ships the whole project at
  exactly Fri 17:00.
- `two_engineers` — alice (backend) + clare (frontend) split the same
  project (`pm/transcript/project_two_engineers.md`), 40 h each, zero slack:
  every odd task blocks the partner's next even task just-in-time. Personas
  default to the stalling mix (alice `free_spirit`, clare `heads_down`);
  `build(member_persona=PERFECT)` finishes the board at exactly Fri 17:00.

**Personas** (baked into each scenario; see `pm/npc/persona.py`):

- `perfect` — works by priority, respects dependencies, closes work on finish.
- `heads_down` — works fine but leaves finished work in `in_review` until a
  standup ends or a Slack message names them; unclosed work stalls the
  partner's cross handoffs until the next standup closes it (or forever, on a
  board with no meetings).
- `free_spirit` — picks at seeded random from everything on their plate,
  blocked tickets included; ignores priority.

**How a run moves** — NPC work is **completion-driven** (`pm/sim/npc.py::
WorkDriver`): one kickoff sweep dispatches everyone's first ticket as a
`jira_work` activity, and every activity completion re-sweeps the roster (a
finish can unblock anyone). There is no per-tick polling. Meetings preempt work
through the bridged `meeting` activity — in-progress work is interrupted and
resumes with its remaining minutes intact, so interruptions are lossless; a
zero-slack board still lands exactly on Fri 17:00. The standup/Slack close
reactions run in every `pm sim` week.

**PM levers** — the world reacts to Slack (`pm/sim/npc.py`): each person named
in a message reads it a seeded-random 1–60 minutes later (`_on_slack_send`
schedules the `slack.read`); on read (`_on_slack_read`) they close their
`in_review` work, and a "please pick up <KEY>" directive bumps that ticket to
priority 0 — the level even a free spirit works first, preempting their current
ticket. These are the levers a PM agent steers with (the agent's only
*action* tool is `send_slack`). In `single_engineer_with_agent`,
`two_engineers_with_agent`, and `team_no_jira_with_agent` the PM is
an **LLM** reviewing the run every four sim-hours through those tools — a
scenario may expose
`agent_review_hook`, which `pm sim` composes into the run automatically. Under
`pm sim` the hook needs `OPENROUTER_API_KEY` in `.env` (model from
`OPENROUTER_MODEL`); every model round-trip and tool call is logged with token
usage to `runs/<name>/agent-<model>.jsonl` (and mirrored into the run's
`event_log`), summed by `pm eval` and plotted by `pm viz`
(`agent_activity.html`).

Every meeting leaves a transcript when it ends; meetings without authored
notes leave an empty one. The authored set (`pm/transcript/no-jira-*.md`)
belongs to the no-Jira scenario below; the agent reviews transcripts via the
`read_transcripts` tool.

**Tasks that never reach Jira** — the "Meeting Transcripts v1" project ships
with three board-sized breakdowns (`pm/transcript/project_{single_engineer,
two_engineers,team}.md` — same project and scope, different work tasks).
`project_team.md` (DRIs, statuses, estimates) is the single source for one more
scenario built by `pm/scenarios/project_board.py`, which files none of the
breakdown as Jira tickets:

- `team_no_jira` — Monday's kickoff establishes all 25 tasks (a zero-slack
  week: 2,220 min per DRI after the meeting fabric) with DRIs **in the meeting
  notes only** (the informal `task` table mirrors them as each meeting ends);
  by Friday 15 are done, five carry over mid-flight, five never started, and
  the Jira board never held a ticket. `read_jira_board` says nothing is
  happening; `read_transcripts` tells the truth. Eval: PROJECT NOT DONE —
  15/25 (source: notes), 0h of Jira tickets closed.

`pm eval` grades exactly this: project completion from the informal task table
(falling back to the Jira board when a run has none, as on the boards above),
plus the hours of Jira tickets closed — no deadline check.

## The board scenarios

| # | Cast | Personas | Verified outcome |
|---|------|----------|------------------|
| 1 | single engineer | `free_spirit` | 10/14 done — **3 project tasks left** at Fri 17:00, displaced by backlog picks |
| 2 | two engineers | alice=`free_spirit`, clare=`heads_down` | **PROJECT NOT DONE** — 8/16; no meetings on this board, so clare's `in_review` work never closes and handoffs stall |

## Running them

Each row is its own scenario, with its personas baked in — the scenario name is the
run id and output folder, so no other flags are needed:

```bash
uv run pm sim --scenario single_engineer  # 1
uv run pm sim --scenario two_engineers          # 2
uv run pm sim --scenario team_no_jira                      # the notes-only week
```

Three more scenarios round out the set — the same three weeks with the LLM PM
attached: `single_engineer_with_agent` (the free-spirit solo board),
`two_engineers_with_agent` (the stalling pair), and
`team_no_jira_with_agent` (the notes-only week, where only a PM that reads the
transcripts sees the real project). A PM that steers nothing reproduces the
unmanaged rows above, which is what the hermetic tests pin with a scripted
fake model.

Then `uv run pm eval --scenario <name>` for the goal line (also written to
`runs/<name>/eval.json`) and `uv run pm viz --scenario <name>` for the calendars +
ticket timeline. Everything for a scenario lives under `runs/<name>/`.

## Driving a run with the LLM agent

The LLM agent's only *action* tool is `send_slack`, so it steers the world
through the two Slack levers above. Inside a simulated week it runs as a
scenario's `agent_review_hook`, which the runner (`pm/scenarios/runner.py`)
composes into the sim loop — `pm sim --scenario single_engineer_with_agent`
is the live example (needs `OPENROUTER_API_KEY`), leaving
`runs/<name>/agent-<model>.jsonl` for `pm eval` (token totals) and `pm viz`
(`agent_activity.html` timeline). To exercise the LLM loop itself against a
seeded throwaway board, run `uv run python examples/run_agent_llm.py` — see the
README's [Agent](../../README.md#agent) section.
