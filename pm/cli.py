"""Operator CLI.

``sim`` builds a scenario run and drives its work week; ``eval`` grades the
outcome; ``viz`` renders the run's board and calendars to static HTML. The run
id defaults to the scenario name (``pm sim --scenario tight_week`` ->
``runs/tight_week/``), and the world database stays open to ad-hoc SQLite
inspection at ``runs/<run_id>/world.db``.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from pm.db.store import Store
from pm.env.environment import Env
from pm.eval import evaluate, format_report, to_dict
from pm.exceptions import ConfigurationError
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.behavior import assignee_pickup_hook
from pm.scenarios import test_dependency_blind_2, tight_week
from pm.sim.simulation import Simulation
from pm.viz import write_calendars, write_jira_tasks

# Scenarios `pm sim` can build and drive (module must expose build/MEMBERS/PROJECT_ID).
SCENARIOS = {
    "test_dependency_blind_2": test_dependency_blind_2,
    "tight_week": tight_week,
}

app = typer.Typer(
    add_completion=False,
    help="Project Manager Simulation Environment — operator CLI.",
)

RUNS_DIR = Path("runs")


def _db_path(run_id: str) -> Path:
    return RUNS_DIR / run_id / "world.db"


@app.command()
def sim(
    run_id: str | None = typer.Option(
        None, "--run-id", help="Run id (default: the scenario name)."
    ),
    scenario: str | None = typer.Option(
        None, "--scenario",
        help=f"Scenario to build if the run does not exist ({' | '.join(SCENARIOS)}).",
    ),
) -> None:
    """Run the simulated work week: NPC coworkers work the board until Fri 17:00."""
    if run_id is None and scenario is None:
        raise typer.BadParameter(
            "pass --scenario to build a run and/or --run-id to continue one "
            f"(scenarios: {', '.join(SCENARIOS)}).", param_hint="--scenario")
    if scenario is not None and scenario not in SCENARIOS:
        raise typer.BadParameter(
            f"unknown scenario {scenario!r} (choices: {', '.join(SCENARIOS)}).",
            param_hint="--scenario")
    rid = run_id if run_id is not None else scenario
    assert rid is not None  # at least one of run_id/scenario is set above

    path = _db_path(rid)
    if not path.exists():
        if scenario is None:
            raise typer.BadParameter(
                f"no run database at {path}; pass --scenario to build one "
                f"(choices: {', '.join(SCENARIOS)}).", param_hint="--scenario")
        env = SCENARIOS[scenario].build(run_id=rid)
        typer.echo(f"Built scenario '{scenario}' at {path.parent}/")
    else:
        env = Env.load(rid)

    name = env.store.get_meta("scenario") or ""
    if scenario is not None and name != scenario:
        env.close()
        raise typer.BadParameter(
            f"run '{rid}' was seeded with scenario {name!r}, not {scenario!r}.",
            param_hint="--scenario")
    module = SCENARIOS.get(name)
    if module is None:
        env.close()
        raise typer.BadParameter(
            f"run '{rid}' was seeded with scenario {name!r}, which `pm sim` "
            f"cannot drive (known: {', '.join(SCENARIOS)}).", param_hint="--run-id")

    simulation = Simulation(env)
    if simulation.is_over():
        typer.echo(f"Run '{rid}' is already at week end ({simulation.now_label()}).")
        typer.echo(f"Evaluate it with:  uv run pm eval --run-id {rid}")
        env.close()
        return

    api = JiraApi(JiraRepository(env.store), env.engine)
    start = simulation.now_label()
    summary = simulation.run(
        on_tick=assignee_pickup_hook(api, module.MEMBERS, module.PROJECT_ID))
    typer.echo(f"Simulated {start} -> {simulation.now_label()} "
               f"(tick {summary.final_tick}); {summary.events_fired} event transitions fired.")
    typer.echo(f"Evaluate it with:  uv run pm eval --run-id {rid}")
    env.close()


@app.command("eval")
def eval_cmd(
    run_id: str = typer.Option(..., "--run-id", help="Run to evaluate."),
    project: str | None = typer.Option(
        None, "--project", help="Project id (default: the run's only project)."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit the evaluation report as JSON."
    ),
) -> None:
    """Evaluate the run's Jira outcomes: hours completed, week goal, who did what."""
    path = _db_path(run_id)
    if not path.exists():
        raise typer.BadParameter(
            f"no run database at {path}; create one with `pm sim --scenario <name>`.")

    store = Store.open(str(path))
    try:
        report = evaluate(store, project_id=project)
    except ConfigurationError as e:
        raise typer.BadParameter(f"{e.message} {e.details}", param_hint="--project") from e
    finally:
        store.close()
    typer.echo(json.dumps(to_dict(report), indent=2) if json_out else format_report(report))


@app.command()
def viz(
    run_id: str = typer.Option(..., "--run-id", help="Run to visualize."),
) -> None:
    """Render the run's Jira board and per-person calendars to static HTML."""
    try:
        for out in (write_calendars(run_id), write_jira_tasks(run_id)):
            typer.echo(str(out.resolve()))
    except ConfigurationError as e:
        raise typer.BadParameter(
            f"{e.message}; create the run with `pm sim --scenario <name>`.",
            param_hint="--run-id") from e


if __name__ == "__main__":
    app()
