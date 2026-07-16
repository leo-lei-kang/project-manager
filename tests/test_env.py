"""Env facade: make/load/reset/close and the seed vs current split."""

from __future__ import annotations

import pytest

from pm.env import Env
from pm.exceptions import ConfigurationError


@pytest.fixture
def env(tmp_path):
    e = Env.make(run_id="t", seed=7, root=tmp_path)
    yield e
    e.close()


def test_make_creates_world_and_seed(env, tmp_path) -> None:
    assert Env.world_path("t", tmp_path).exists()
    assert Env.seed_path("t", tmp_path).exists()
    assert env.store.get_meta("seed") == "7"
    assert env.clock.now() == 0


def test_make_refuses_existing_without_force(tmp_path) -> None:
    Env.make(run_id="t", root=tmp_path).close()
    with pytest.raises(ConfigurationError):
        Env.make(run_id="t", root=tmp_path)
    # force overwrites cleanly
    Env.make(run_id="t", root=tmp_path, force=True).close()


def test_seed_db_is_immutable_snapshot(env) -> None:
    # Mutating current must not change the seed snapshot.
    env.store.set_tick(120)
    assert env.db("current").query("SELECT value FROM meta WHERE key='current_tick'").rows[0][0] == "120"
    seed_tick = env.db("seed").query("SELECT value FROM meta WHERE key='current_tick'").rows[0][0]
    assert seed_tick == "0"


def test_reset_restores_from_seed(env) -> None:
    env.clock.advance(300)
    assert env.clock.now() == 300
    env.reset()
    assert env.clock.now() == 0  # back to the seed state


def test_reset_can_override_seed(env) -> None:
    env.reset(seed=99)
    assert env.store.get_meta("seed") == "99"


def test_db_rejects_unknown_name(env) -> None:
    with pytest.raises(ConfigurationError):
        env.db("bogus")


def test_load_existing_run(tmp_path) -> None:
    Env.make(run_id="t", seed=5, root=tmp_path).close()
    e = Env.load("t", root=tmp_path)
    assert e.store.get_meta("seed") == "5"
    e.close()


def test_load_missing_run_raises(tmp_path) -> None:
    with pytest.raises(ConfigurationError):
        Env.load("nope", root=tmp_path)


def test_verify_runs_env_first_callable(env) -> None:
    def scorer(e, threshold: int) -> float:
        return 1.0 if e.clock.now() <= threshold else 0.0

    assert env.verify(scorer, 10) == 1.0
