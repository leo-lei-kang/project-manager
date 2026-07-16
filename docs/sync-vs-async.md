# Guideline: Synchronous vs. Asynchronous in the Simulation

The sync/async split is the central systems choice in the kernel. Follow this guideline
when deciding how a new behaviour should enter the world.

**Rule of thumb:** if the **agent** does it and it takes effect **now**, it's
*synchronous*. If the **world** does it — later, or over a span of time — it's
*asynchronous*, and it must be a durative `Event` on the queue.

---

## Synchronous — the agent's own action

The agent's effect resolves **at the current tick** (no sim-time passes while it runs);
only then does the clock advance by the action's `cost`.

`pm/sim/engine.py` — `Engine.perform_action(actor, cost, effect)`:

```python
def perform_action(self, actor, cost, effect=None):
    if effect is not None:
        effect()                 # applies NOW, at the current tick — instantaneous
    self.store.log_event(now, actor=actor, kind="action")
    return self.advance(cost)    # THEN sim-time moves forward by `cost`
```

**Use cases** — anything the agent does that lands immediately in the world:

- Post a Slack message, send an email, assign a Jira ticket, book a meeting, edit a doc.
- The state change is applied at "now"; `cost` is how much simulated time the action
  consumed (skim a thread ≈ a few minutes; write a spec ≈ an hour).

**Why it matters:** it decouples the world from wall-clock inference latency. Whether the
model thinks for 200 ms or 30 s, the action costs exactly `cost` sim-ticks, and the agent
always acts against a consistent, frozen "now".

---

## Asynchronous — everything the world does on its own

Durative `Event`s sit on the persisted queue and are driven **purely by the clock**. As
sim-time advances one work-minute at a time (`Engine.step`, `pm/sim/engine.py`), the
engine **starts** events whose `start_tick` has arrived and **completes** those whose
`duration` has elapsed. Nothing runs on a real thread.

**Use cases** — anything that happens *without* the agent doing it right then, especially
with delay or duration (see `pm/sim/events.py`):

| Event | Async behaviour |
|-------|-----------------|
| `slack.send` | a chat message becomes visible *after* a send/typing latency (not instantly) |
| `jira_ticket` | a coworker works a Jira ticket over its estimate; completion later unblocks dependents |
| `meeting` | a meeting spans time; the transcript is readable only when it ends |
| `email.send` | arrives in recipients' inboxes after a delivery latency |
| `email.read` | a recipient reads a delivered email (awareness only; no world write) |

**Why it matters:** it is what makes the world feel alive and creates the PM problem —
information arrives late, coworkers have response lag, work takes time, deadlines approach
on their own.

---

## How they meet

The two connect inside a single `perform_action`: the sync effect lands now, then
advancing by `cost` **delivers any async events that come due in that window** — returned
as the agent's observation.

```
agent sends a message   →  (sync)  message posted at `now`
   → advance by cost     →  (async) a coworker's message, scheduled 90 ticks earlier,
                                    fires during this window and is returned
```

Concretely, `examples/run_week.py` shows durative events (a coworker's message, task
work, meetings) starting and completing on the clock as the bounded `Simulation`
loop advances the week one work-minute at a time.

---

## Quick reference

| | Synchronous | Asynchronous |
|---|---|---|
| **Who** | the agent | the world (NPCs, meetings, task work, deadlines) |
| **When** | at the current tick, instantly | later / over time, as the clock advances |
| **Mechanism** | `Engine.perform_action(effect)` | durative `Event`s drained by `Engine.step` |
| **Timing** | costs `cost` ticks, wall-clock-free | governed by `start_tick` + `duration` |
| **Example** | PM posts a status update | Priya replies 4h later; a meeting runs 30 min |

## The invariant

**Only advancing the clock delivers async events, and the agent only advances the clock
by acting.** So the world moves in lockstep with the agent's actions — deterministically,
and independent of real (wall-clock) time.

## When you add a new behaviour

1. Does the **agent** trigger it and should it take effect **immediately**? → apply it as
   a synchronous `effect` inside `perform_action`.
2. Does it happen **later**, **over a span**, or is it driven by a **coworker/deadline**?
   → model it as a durative `Event` (`pm/sim/events.py`) scheduled on the queue; never
   simulate delay with real threads or wall-clock sleeps.
3. Keep all mutation flowing through `Store` (the single writer) so state stays
   consistent and replayable.
