"""Scenario generator — assemble a scenario by calling each per-event builder.

Seeds a world (the consolidated cast + channels + a Jira issue pool), then invokes
every builder at a chosen intensity :class:`Level` and schedules
the resulting events. ``generate_all`` walks the levels ascending (few -> frequent ->
aggressive) so it "starts with the less frequent cases".

Run it:  ``uv run python -m pm.scenarios.generator``
"""

from __future__ import annotations

from pathlib import Path

from pm.env.environment import RUNS_DIR, Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.cast import MEMBERS, STAKEHOLDERS, AGENT, seed_cast
from pm.scenarios.builders import BUILDERS, LEVEL_ORDER, BuildContext, EventBuilder, Level
from pm.world.models import Project

_CHANNELS = ["eng", "general"]
_ISSUE_POOL = 12  # enough issues for the aggressive JiraTask count


class ScenarioGenerator:
    def __init__(self, builders: list[EventBuilder] | None = None) -> None:
        self.builders = builders if builders is not None else BUILDERS

    def _seed_world(self, env: Env) -> BuildContext:
        seed_cast(env.store)
        for cid in _CHANNELS:
            env.store.db.execute(
                "INSERT INTO channel (id, name, kind) VALUES (?, ?, 'channel')", (cid, cid)
            )
        env.store.add_project(Project(id="GEN", name="Generated scenario"))

        repo = JiraRepository(env.store)
        repo.ensure_schema()
        api = JiraApi(repo, env.engine)
        members = [c.id for c in MEMBERS]
        epic = api.create_issue("GEN", "epic", "Delivery", actor=AGENT.id)
        issue_keys: list[str] = []
        for i in range(_ISSUE_POOL):
            issue = api.create_issue(
                "GEN", "task", f"Task {i + 1}", parent=epic.id,
                estimate_minutes=90, assignee=members[i % len(members)], actor=AGENT.id,
            )
            issue_keys.append(issue.id)

        return BuildContext(
            members=members,
            stakeholders=[c.id for c in STAKEHOLDERS],
            agent=AGENT.id,
            channels=list(_CHANNELS),
            issue_keys=issue_keys,
        )

    def generate(self, level: Level, *, root: Path = RUNS_DIR) -> Env:
        """Seed a world and schedule one scenario at ``level``; returns its Env."""
        run_id = f"gen-{level.value}"
        env = Env.make(scenario=f"gen_{level.value}", run_id=run_id, seed=42,
                       force=True, root=root)
        ctx = self._seed_world(env)
        for builder in self.builders:
            for event in builder.build(ctx, level):
                env.engine.schedule(event)
        # Re-snapshot so the fully-seeded, fully-scheduled world is the baseline.
        env.store.db.backup_to(Env.seed_path(run_id, root))
        return env

    def generate_all(self, *, root: Path = RUNS_DIR):
        """Yield (level, Env) for each level, ascending (few -> frequent -> aggressive)."""
        for level in LEVEL_ORDER:
            yield level, self.generate(level, root=root)


def _counts_by_type(env: Env) -> dict[str, int]:
    rows = env.store.db.query_all(
        "SELECT type, COUNT(*) AS n FROM event GROUP BY type ORDER BY type"
    )
    return {r["type"]: r["n"] for r in rows}


def main() -> None:
    import tempfile

    gen = ScenarioGenerator()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        print("Generating scenarios (ascending intensity)\n")
        totals: dict[Level, int] = {}
        per_type: dict[Level, dict[str, int]] = {}
        for level, env in gen.generate_all(root=root):
            counts = _counts_by_type(env)
            per_type[level] = counts
            totals[level] = sum(counts.values())
            env.close()

        types = sorted({t for c in per_type.values() for t in c})
        header = f"{'event type':<16}" + "".join(f"{lv.value:>12}" for lv in LEVEL_ORDER)
        print(header)
        print("-" * len(header))
        for t in types:
            row = f"{t:<16}" + "".join(f"{per_type[lv].get(t, 0):>12}" for lv in LEVEL_ORDER)
            print(row)
        print("-" * len(header))
        print(f"{'TOTAL':<16}" + "".join(f"{totals[lv]:>12}" for lv in LEVEL_ORDER))


if __name__ == "__main__":
    main()
