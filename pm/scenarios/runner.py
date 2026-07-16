"""Drive a scenario module's work week — the shared sim loop for `pm sim` and tests.

Completion-driven: a :class:`~pm.sim.npc.WorkDriver` sweeps the roster once at
kickoff, then re-sweeps from the two completion hooks — the ``ActivityManager``'s
``on_activity_done`` (every finished activity) and the ``Engine``'s
``on_event_done`` (standup closes; Slack effects land when the named person
*reads* the message, within the hour of the send). The per-tick ``on_tick``
carries only the optional PM ``agent_review_hook``. Runs to Fri 17:00, then
fires one final PM close-out for work that finished on the last tick.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.sim.npc import WorkDriver
from pm.sim.simulation import RunSummary, Simulation

if TYPE_CHECKING:
    from pm.env.environment import Env


def drive(env: "Env", module: Any) -> RunSummary:
    """Run ``module``'s week on ``env`` (kickoff sweep + optional PM); return the summary.

    ``module`` must expose ``MEMBERS`` and ``PROJECT_ID``; it may expose
    ``agent_review_hook(env)`` to add a PM review hook.
    """
    api = JiraApi(JiraRepository(env.store), env.engine)
    driver = WorkDriver(api, module.MEMBERS, module.PROJECT_ID)
    env.engine.activities.on_activity_done = driver.on_activity_done
    env.engine.on_event_done = driver.on_event_done
    review = getattr(module, "agent_review_hook", None)
    review_hook = review(env) if review is not None else None
    sim = Simulation(env)
    driver.sweep(env.engine)  # kickoff: everyone picks their first ticket
    summary = sim.run(on_tick=review_hook)
    if review_hook is not None:
        review_hook(sim)  # week-end close-out for work finishing on the final tick
    return summary
