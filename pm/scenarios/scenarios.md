# Scenario catalog — personas × boards × a PM in the loop

Eight verified configurations built from three boards, three behavior personas,
and an optional scripted PM. Every row below was actually run; the outcome
column is the observed `pm eval` result (seed 42).

**Boards** (each `pm/scenarios/<name>.py`; `*_with_pm` variants add the PM):

- `test_single_engineer` — alice alone, 60 h of tickets in a 40-h week: 7
  high-priority launch blockers (35 h) + 5 backlog items (25 h). Exactly eight
  tasks fit; triage decides *which* eight. The goal line is NOT ACCOMPLISHED by
  design — the success criterion is "all 7 launch blockers ship".
- `test_two_engineers` — alice (backend) + clare (frontend), 40 h each, zero
  slack: every odd task blocks the partner's next even task just-in-time.
- `tight_week` — the five-member team + meetings, 66 tasks tiling every
  member's meeting-free calendar exactly; zero slack.

**Personas** (`--persona`, see `pm/npc/persona.py`):

- `perfect` — works by priority, respects dependencies, closes work on finish.
- `heads_down` — works fine but leaves finished work in `in_review` until a
  standup ends or a Slack message names them; on zero-slack boards the unclosed
  work stalls the partner's cross handoffs.
- `free_spirit` — picks at seeded random from everything on their plate,
  blocked tickets included; ignores priority.

**The PM** (`pm/npc/scripted_pm.py`) acts only through Slack, via the two
levers the world reacts to (`pm/npc/reactions.py:_on_slack_send`): naming a
person closes their `in_review` work, and a "please pick up <KEY>" directive
bumps that ticket to priority 0 — the level even a free spirit works first.
The `*_with_pm` scenarios expose `agent_review_hook`, which `pm sim` composes
into the run automatically. (The visibility-only variant
`test_single_engineer_with_agent` posts highlights that steer nothing — the
contrast case.)

## The eight scenarios

| # | Cast | Personas | PM | Verified outcome |
|---|------|----------|----|------------------|
| 1 | single engineer | `perfect` | — | 8/12 done; **all 7 launch blockers ship** |
| 2 | single engineer | `free_spirit` | — | 8/12 done; **2 launch blockers left** at Fri 17:00 |
| 3 | single engineer | `free_spirit` | scripted | 8/12 done; **all 7 launch blockers ship** — directives pin her picks |
| 4 | two engineers | `perfect` | — | **ACCOMPLISHED** — 16/16, last completion exactly Fri 17:00 |
| 5 | two engineers | alice=`free_spirit`, clare=`heads_down` | — | **NOT ACCOMPLISHED** — 8/16; clare's work sits `in_review`, handoffs stall |
| 6 | two engineers | alice=`free_spirit`, clare=`heads_down` | scripted | **ACCOMPLISHED** — 16/16 at exactly Fri 17:00 |
| 7 | team (tight_week) | mixed: bob+elieen `free_spirit`, clare `heads_down`, rest `perfect` | — | **NOT ACCOMPLISHED** — 45/66 |
| 8 | team (tight_week) | same mix | scripted | **ACCOMPLISHED** — 66/66 at exactly Fri 17:00 |

## Running them

```bash
# 1-2: the solo board, triage decided by persona
uv run pm sim --scenario test_single_engineer
uv run pm sim --scenario test_single_engineer --run-id solo-free --persona free_spirit

# 3: same free-spirit engineer, actively directed over Slack
uv run pm sim --scenario test_single_engineer_with_pm

# 4-5: the cross-blocked pair, uniform vs mixed personas
uv run pm sim --scenario test_two_engineers
uv run pm sim --scenario test_two_engineers --run-id pair-mixed \
              --persona alice=free_spirit,clare=heads_down

# 6: the same mixed pair, managed
uv run pm sim --scenario test_two_engineers_with_pm \
              --persona alice=free_spirit,clare=heads_down

# 7-8: the saturated team week, mixed personas, unmanaged vs managed
uv run pm sim --scenario tight_week --run-id team-mixed \
              --persona bob=free_spirit,clare=heads_down,elieen=free_spirit
uv run pm sim --scenario tight_week_with_pm \
              --persona bob=free_spirit,clare=heads_down,elieen=free_spirit
```

Then `uv run pm eval --run-id <id>` for the goal line and
`uv run pm viz --run-id <id>` for the calendars + ticket timeline. `--persona`
takes one preset for everyone or `member=preset` pairs (unnamed members keep
their cast default, `perfect`).

## Why each managed run succeeds

- **Row 3**: each time alice is about to pick, the PM posts "please pick up
  <KEY>" for her highest-priority open ticket; the bump to priority 0 overrides
  freestyle, so the seven launch blockers ship first.
- **Row 6**: the PM closes clare's `in_review` work the same tick it appears
  (keeping the just-in-time cross handoffs intact) and directs alice's picks
  back to ordinal order; the board finishes with zero idle minutes.
- **Row 8**: both levers across five members; the zero-slack schedule holds
  because the PM's closes and directives land in the same tick as the work
  they react to, plus a week-end close-out for work finishing on the final tick.

## Swapping in the real LLM agent

The scripted PM is a deterministic stand-in for the agent-under-test: the
`pm-agent` LLM's only *action* tool is `send_slack`, so it steers the world
through exactly the same two levers — its messages just arrive on the model's
own review cadence instead of every tick. To drive a run with the real agent,
seed a scenario without simulating (`uv run python -m
pm.scenarios.test_single_engineer_with_pm`), serve the tools
(`PM_RUN_ID=test_single_engineer_with_pm uv run pm-mcp`), and drive it with
`uv run pm-agent "..."` — see the README's [Agent](../../README.md#agent)
section.
