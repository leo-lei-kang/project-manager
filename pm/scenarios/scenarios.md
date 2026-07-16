# Scenario catalog — personas × boards

Five verified configurations built from three boards and three behavior
personas. Every row below was actually run; the outcome column is the observed
`pm eval` result (seed 42).

**Boards** (each `pm/scenarios/<name>.py`):

- `test_single_engineer` — alice alone, 60 h of tickets in a 40-h week: 7
  high-priority launch blockers (35 h) + 5 backlog items (25 h). Exactly eight
  tasks fit; triage decides *which* eight. The goal line is PROJECT NOT DONE by
  design — the success criterion is "all 7 launch blockers ship".
- `test_two_engineers` — alice (backend) + clare (frontend), 40 h each, zero
  slack: every odd task blocks the partner's next even task just-in-time.
- `team_with_jira` — the five-member team + meetings, 66 tasks tiling every
  member's meeting-free calendar exactly; zero slack.

**Personas** (baked into each scenario; see `pm/npc/persona.py`):

- `perfect` — works by priority, respects dependencies, closes work on finish.
- `heads_down` — works fine but leaves finished work in `in_review` until a
  standup ends or a Slack message names them; on zero-slack boards the unclosed
  work stalls the partner's cross handoffs.
- `free_spirit` — picks at seeded random from everything on their plate,
  blocked tickets included; ignores priority.

**PM levers** — the world reacts to Slack (`pm/npc/reactions.py:_on_slack_send`):
a message naming a person closes their `in_review` work, and a "please pick up
<KEY>" directive bumps that ticket to priority 0 — the level even a free spirit
works first. These are the levers a PM agent steers with (the agent's only
*action* tool is `send_slack`). The visibility-only variant
`test_single_engineer_with_agent` posts highlights that steer nothing — the
contrast case; a scenario may expose `agent_review_hook`, which `pm sim`
composes into the run automatically.

On the team boards, each standup also leaves a markdown transcript
(`pm/transcript/`, persisted when the meeting ends) carrying an unresolved
thread the PM must triage: a customer request that is *not* on the Jira board
— Bob offers to build it, Alice insists on the high-priority launch work. The
agent reviews these via the `read_transcripts` tool. (Every meeting now leaves
a transcript when it ends; meetings without authored notes leave an empty one.)

**Tasks that never reach Jira** — `pm/transcript/project.md` defines the
"Meeting Transcripts v1" project and its task breakdown (DRIs, statuses,
estimates); it is the single source for two more scenarios built by
`pm/scenarios/project_board.py`, which files only a *selected subset* of the
breakdown as Jira tickets:

- `team_no_jira` — Monday's kickoff establishes all six tasks with DRIs **in
  the meeting notes only** (the informal `task` table mirrors them as each
  meeting ends); by Friday four are done and two carry over, and the Jira
  board never held a ticket. `read_jira_board` says nothing is happening;
  `read_transcripts` tells the truth. Eval: PROJECT NOT DONE — 4/6 (source:
  notes), 0h of Jira tickets closed.
- `team_partial_jira` — three of the six were filed (and get worked on the
  board as usual); the other three live only in the notes. The board looks
  healthy but the project is twice its size. Eval: PROJECT NOT DONE — 4/6
  (source: notes), all 20h of filed tickets closed.

`pm eval` grades exactly this: project completion from the informal task table
(falling back to the Jira board when a run has none, as on the boards above),
plus the hours of Jira tickets closed — no deadline check.

## The five scenarios

| # | Cast | Personas | Verified outcome |
|---|------|----------|------------------|
| 1 | single engineer | `perfect` | 8/12 done; **all 7 launch blockers ship** |
| 2 | single engineer | `free_spirit` | 8/12 done; **2 launch blockers left** at Fri 17:00 |
| 3 | two engineers | `perfect` | **PROJECT DONE** — 16/16, last completion exactly Fri 17:00 |
| 4 | two engineers | alice=`free_spirit`, clare=`heads_down` | **PROJECT NOT DONE** — 8/16; clare's work sits `in_review`, handoffs stall |
| 5 | team (team_with_jira) | mixed: bob+elieen `free_spirit`, clare `heads_down`, rest `perfect` | **PROJECT NOT DONE** — 45/66 |

## Running them

Each row is its own scenario, with its personas baked in — the scenario name is the
run id and output folder, so no other flags are needed:

```bash
uv run pm sim --scenario test_single_engineer              # 1
uv run pm sim --scenario test_single_engineer_free_spirit  # 2
uv run pm sim --scenario test_two_engineers                # 3
uv run pm sim --scenario test_two_engineers_mixed          # 4
uv run pm sim --scenario team_mixed_persona                  # 5
```

Two more scenarios round out the set: `team_with_jira` (the team all `perfect` —
66/66 PROJECT DONE) and `test_single_engineer_with_agent` (the visibility-only
PM: highlights that steer nothing, so the outcome equals row 2).

Then `uv run pm eval --scenario <name>` for the goal line (also written to
`runs/<name>/eval.json`) and `uv run pm viz --scenario <name>` for the calendars +
ticket timeline. Everything for a scenario lives under `runs/<name>/`.

## Driving a run with the LLM agent

The LLM agent's only *action* tool is `send_slack`, so it steers the world
through the two Slack levers above. Inside a simulated week it runs as a
scenario's `agent_review_hook`, which the runner (`pm/scenarios/runner.py`)
composes into the sim loop. To exercise the LLM loop itself against a seeded
throwaway board, run `uv run python examples/run_agent_llm.py` — see the
README's [Agent](../../README.md#agent) section.
