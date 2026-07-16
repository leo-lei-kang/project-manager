"""The `Env` facade — one handle over a simulation run.

Mirrors fleet-sdk's ``SyncEnv`` (``fleet/client.py``): a single object that owns
the world store and the sim kernel, and exposes typed accessors used for setup,
driving, and grading. It is the object a scorer or the simulation loop receives.

Two databases per run, following fleet's ``env.db("seed")`` vs ``env.db("current")``:

  * ``runs/<id>/world.db`` — the live, mutating world (the single source of truth).
  * ``runs/<id>/seed.db``  — an immutable snapshot of the initial state, taken at
    ``make`` time, so a grader can diff seed → current and forbid unintended change.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from pm.db.store import Store
from pm.exceptions import ConfigurationError
from pm.world.resources import ResourceMode, WorldResource
from pm.sim.clock import SimClock
from pm.sim.engine import Engine
from pm.sim.scheduler import Scheduler

RUNS_DIR = Path("runs")


class Env:
    def __init__(self, run_id: str, store: Store, root: Path = RUNS_DIR) -> None:
        self.run_id = run_id
        self.root = root
        self.store = store
        self.clock = SimClock(store)
        self.scheduler = Scheduler(store, self.clock)
        self.engine = Engine(store, self.clock, self.scheduler)
        self._seed_resource: WorldResource | None = None

    # -- paths ---------------------------------------------------------------

    @classmethod
    def world_path(cls, run_id: str, root: Path = RUNS_DIR) -> Path:
        return root / run_id / "world.db"

    @classmethod
    def seed_path(cls, run_id: str, root: Path = RUNS_DIR) -> Path:
        return root / run_id / "seed.db"

    # -- construction --------------------------------------------------------

    @classmethod
    def make(
        cls,
        scenario: str = "first_week",
        run_id: str = "demo",
        seed: int = 42,
        *,
        force: bool = False,
        root: Path = RUNS_DIR,
    ) -> "Env":
        """Create a fresh run: seed ``world.db`` meta and snapshot it to ``seed.db``.

        ``run_id`` defaults to ``"demo"``; pass one (e.g. ``"test"``) for a distinct
        run. Populating the world with content (people/tasks/etc.) is done by the
        caller or a scenario seeder; ``make`` establishes the run + immutable seed
        snapshot that grading diffs against.
        """
        world = cls.world_path(run_id, root)
        if world.exists():
            if not force:
                raise ConfigurationError(
                    f"run {run_id!r} already exists at {world}; pass force=True to overwrite",
                    details={"run_id": run_id, "path": str(world)},
                )
            cls._remove_db(world)
            cls._remove_db(cls.seed_path(run_id, root))

        store = Store.open(str(world), create=True)
        store.set_meta("scenario", scenario)
        store.set_meta("seed", str(seed))
        store.set_tick(0)
        store.log_event(0, actor="operator", kind="run.init", payload={"seed": seed, "scenario": scenario})

        # Snapshot the initial state as the immutable seed database.
        store.db.backup_to(cls.seed_path(run_id, root))
        return cls(run_id, store, root)

    @classmethod
    def load(cls, run_id: str = "demo", *, root: Path = RUNS_DIR) -> "Env":
        """Open an existing run's world database."""
        world = cls.world_path(run_id, root)
        if not world.exists():
            raise ConfigurationError(
                f"no run database at {world}; create it with Env.make first",
                details={"run_id": run_id, "path": str(world)},
            )
        return cls(run_id, Store.open(str(world)), root)

    def reset(self, seed: int | None = None) -> None:
        """Restore ``world.db`` from the immutable seed snapshot.

        Replays start from an identical initial state — the basis for
        reproducible grading. Optionally overrides the stored seed.
        """
        seed_db = self.seed_path(self.run_id, self.root)
        if not seed_db.exists():
            raise ConfigurationError(
                f"no seed snapshot at {seed_db}; cannot reset",
                details={"run_id": self.run_id},
            )
        self._close_seed()
        self.store.close()
        world = self.world_path(self.run_id, self.root)
        self._remove_db(world)
        shutil.copyfile(seed_db, world)

        self.store = Store.open(str(world))
        self.clock = SimClock(self.store)
        self.scheduler = Scheduler(self.store, self.clock)
        self.engine = Engine(self.store, self.clock, self.scheduler)
        if seed is not None:
            self.store.set_meta("seed", str(seed))

    def close(self) -> None:
        self._close_seed()
        self.store.close()

    # -- accessors (mirror SyncEnv.db / verify) ------------------------------

    def db(self, which: str = "current") -> WorldResource:
        """Return the world resource: ``"current"`` (live) or ``"seed"`` (initial)."""
        if which == "current":
            return WorldResource(self.store, name="current")
        if which == "seed":
            if self._seed_resource is None:
                seed_db = self.seed_path(self.run_id, self.root)
                if not seed_db.exists():
                    raise ConfigurationError(
                        f"no seed snapshot at {seed_db}", details={"run_id": self.run_id}
                    )
                self._seed_resource = WorldResource(
                    Store.open(str(seed_db)), name="seed", mode=ResourceMode.ro
                )
            return self._seed_resource
        raise ConfigurationError(f"unknown db {which!r}; expected 'current' or 'seed'")

    def verify(self, verifier: Any, *args: Any, **kwargs: Any) -> float:
        """Run an env-first scoring function against this environment.

        A scorer is any callable taking the env first and returning a number/bool;
        exceptions propagate (callers decide how to treat failures).
        """
        return float(verifier(self, *args, **kwargs))

    # -- helpers -------------------------------------------------------------

    def _close_seed(self) -> None:
        if self._seed_resource is not None:
            self._seed_resource.close()
            self._seed_resource = None

    @staticmethod
    def _remove_db(path: Path) -> None:
        for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            p.unlink(missing_ok=True)
