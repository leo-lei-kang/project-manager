"""Authored scenarios — Python seeders that populate a run's world and bake the
content into the immutable ``seed.db`` snapshot.

Each scenario exposes a ``build(...) -> Env`` that seeds people, a Jira board, and any
pre-defined events, then re-snapshots the seed database so the whole starting state is
captured (``Env.make`` snapshots an empty seed before the caller seeds, so scenarios
re-run ``backup_to`` after seeding).

``generator`` adds a composable path: per-event builders + a ``ScenarioGenerator``
(``from pm.scenarios.generator import ScenarioGenerator``) that emits a series of
scenarios at ascending intensity (few -> frequent -> aggressive).
"""

from __future__ import annotations

from pm.scenarios.builders import Level

__all__ = ["Level"]
