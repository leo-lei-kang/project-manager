"""Drive a scenario module's work week — the shared sim loop for `pm sim` and tests.

Composes the members' pickup hook with an optional PM ``agent_review_hook``
(the PM runs *first*, so a same-tick close/directive lands before the person it steers
picks their next ticket), runs to Fri 17:00, then fires one final PM close-out for work
that finished on the last tick.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.behavior import assignee_pickup_hook, compose
from pm.sim.simulation import RunSummary, Simulation

if TYPE_CHECKING:
    from pm.env.environment import Env


def drive(env: "Env", module: Any) -> RunSummary:
    """Run ``module``'s week on ``env`` (pickup + optional PM); return the summary.

    ``module`` must expose ``MEMBERS`` and ``PROJECT_ID``; it may expose
    ``agent_review_hook(env)`` to add a PM review hook.
    """
    api = JiraApi(JiraRepository(env.store), env.engine)
    pickup = assignee_pickup_hook(api, module.MEMBERS, module.PROJECT_ID)
    review = getattr(module, "agent_review_hook", None)
    review_hook = review(env) if review is not None else None
    on_tick = compose(review_hook, pickup) if review_hook is not None else pickup
    sim = Simulation(env)
    summary = sim.run(on_tick=on_tick)
    if review_hook is not None:
        review_hook(sim)  # week-end close-out for work finishing on the final tick
    return summary
