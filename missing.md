# What's still missing

Gap list against the assignment requirements, ranked by visibility to a
reviewer. The systems core (sim time, world model, personas, deterministic
eval, operator flows, scenario scalability) is covered; the gaps below are
concentrated in tool-surface breadth, stakeholder pressure, and grading
documentation.

## 1. Email has no agent surface and no scenario uses it

The machinery exists (`email`/`email_thread` tables, `EmailSendEvent`, the
generator's `EmailBuilder`) but `AgentTools` has no `read_email`/`send_email`,
and no authored scenario seeds a single email. The requirements name email
twice (core loop + integrated tool surfaces).

**Smallest fix:** a `read_email` tool plus one seeded thread in a scenario —
naturally combined with #3 (a stakeholder pressure thread).

## 2. Document management has no surface either

The `document` table and `write_doc`/`review_doc` activity kinds exist but are
dead in practice: no scenario seeds docs, no agent tool reads them.

**Smallest fix:** seed one spec/brief document per scenario and add a
`read_documents` tool.

## 3. No stakeholder pressure in any current scenario

erin/vera/xavier exist in `pm/npc/cast.py` but the current scenarios seed
members only — no stakeholder actors, no pressure events, and the transcripts
never mention them. "Dependencies, blockers, and stakeholder pressure" is an
explicit scope requirement.

**Smallest fix:** seed the stakeholders in `team_no_jira`, add one mid-week
pressure email/Slack thread from a stakeholder (deadline ask the agent must
triage, not obey blindly).

## 4. No proactive outreach toward the agent

Nothing ever messages the PM unprompted. Note also that `runner.drive` builds
`WorkDriver` without `status_channel`, so even the `announces_progress`
status posts are silent in catalog runs.

**Smallest fix:** wire `status_channel` in the runner, and add one reaction
that makes a blocked NPC post "waiting on <KEY>" to `#eng` — a discoverable
signal the agent can act on.

## 5. README lacks the required grading depth

"Enough detail on grading that a reviewer can understand the score
components, inspect example outcomes, and see how the evaluator resists
reward hacking" is a listed deliverable. The story exists (outcome-only
grading from ground-truth task state, pinned do-nothing baselines, the
notes-vs-board trap punishing dashboard gaming, seed-diff auditability) but
is not written in the README.

**Smallest fix:** a "Grading" section: score components, one example
`eval.json`, and a short reward-hacking paragraph.

## 6. Per-person response delays are dead fields

`Person.delay_min`/`delay_max` are seeded and persisted, and the `pm/npc`
docstring promises response latency comes from them — but nothing reads
them; Slack read latency is a global seeded 1–60 min draw.

**Smallest fix:** draw the read delay from the reader's own bounds (or delete
the fields and fix the docstring).

## 7. Two one-line documentation stances

- State explicitly that **no model-based verification** is used — the eval is
  fully deterministic by design (the requirement asks this to be explicit if
  used; saying why it isn't needed is the stronger position).
- Document the **keyless reviewer path**: the `_with_agent` flows are fully
  exercisable through the scripted fake client (tests) without an
  `OPENROUTER_API_KEY`.
