"""Operator CLI.

``sim`` builds a scenario run and drives its work week (narrating the log to
the console as it goes; silence with ``--no-verbose``); ``eval`` grades the
outcome; ``viz`` renders the run's board and calendars to static HTML; ``log``
prints a finished run's sim-time timeline. Every command takes a single
``--scenario`` whose name is the run id and output folder — all results for a
scenario live under ``runs/<scenario>/`` (``world.db``, ``seed.db``,
``eval.json``, ``calendars.html``, ``jira_tasks.html``, and — for scenarios
with an LLM agent — ``agent-<model>.jsonl`` + ``agent_activity.html``).
Personas are baked into each scenario, so there is no runtime persona flag.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from pm.db.store import Store
from pm.env.environment import Env
from pm.eval import evaluate, format_report, to_dict
from pm.exceptions import ConfigurationError
from pm.scenarios import (
    runner,
    team_no_jira,
    team_no_jira_with_agent,
    single_engineer,
    single_engineer_with_agent,
    two_engineers,
    two_engineers_with_agent,
)
from pm.sim.narrate import format_entry
from pm.sim.simulation import Simulation
from pm.viz import write_agent_activity, write_calendars, write_jira_tasks

# Scenarios `pm sim` can build and drive. Each module exposes build/MEMBERS/PROJECT_ID,
# bakes in its personas, and may expose agent_review_hook(env) to add a PM review hook.
SCENARIOS = {
    "team_no_jira": team_no_jira,
    "team_no_jira_with_agent": team_no_jira_with_agent,
    "single_engineer": single_engineer,
    "single_engineer_with_agent": single_engineer_with_agent,
    "two_engineers": two_engineers,
    "two_engineers_with_agent": two_engineers_with_agent,
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
    scenario: str = typer.Option(
        ..., "--scenario",
        help=f"Scenario to build and run ({' | '.join(SCENARIOS)}); also the run id "
             "and output folder runs/<scenario>/.",
    ),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose",
        help="Stream the simulation log to the console as the week runs.",
    ),
) -> None:
    """Run a scenario's simulated work week (Mon 09:00 -> Fri 17:00).

    The scenario name is the run id and output folder: results land in
    ``runs/<scenario>/``. Personas are baked into each scenario (no persona flag).
    Re-running an unfinished scenario continues it in place.
    """
    if scenario not in SCENARIOS:
        raise typer.BadParameter(
            f"unknown scenario {scenario!r} (choices: {', '.join(SCENARIOS)}).",
            param_hint="--scenario")
    module = SCENARIOS[scenario]
    path = _db_path(scenario)
    if not path.exists():
        env = module.build(run_id=scenario)
        typer.echo(f"Built scenario '{scenario}' at {path.parent}/")
    else:
        env = Env.load(scenario)
    if verbose:
        env.store.on_log = lambda e: typer.echo(format_entry(e))

    simulation = Simulation(env)
    if simulation.is_over():
        typer.echo(f"Run '{scenario}' is already at week end ({simulation.now_label()}).")
        typer.echo(f"Evaluate it with:  uv run pm eval --scenario {scenario}")
        env.close()
        return

    start = simulation.now_label()
    summary = runner.drive(env, module)
    typer.echo(f"Simulated {start} -> {simulation.now_label()} "
               f"(tick {summary.final_tick}); {summary.events_fired} event transitions fired.")
    typer.echo(f"Evaluate it with:  uv run pm eval --scenario {scenario}")
    env.close()


@app.command("eval")
def eval_cmd(
    scenario: str = typer.Option(..., "--scenario", help="Scenario run to evaluate."),
    project: str | None = typer.Option(
        None, "--project", help="Project id (default: the run's only project)."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit the evaluation report as JSON to stdout."
    ),
) -> None:
    """Grade the run's project completion and write runs/<scenario>/eval.json."""
    path = _db_path(scenario)
    if not path.exists():
        raise typer.BadParameter(
            f"no run at {path}; create it with `pm sim --scenario {scenario}`.",
            param_hint="--scenario")

    store = Store.open(str(path))
    try:
        report = evaluate(store, project_id=project, run_dir=path.parent)
    except ConfigurationError as e:
        raise typer.BadParameter(f"{e.message} {e.details}", param_hint="--project") from e
    finally:
        store.close()

    out = path.parent / "eval.json"
    out.write_text(json.dumps(to_dict(report), indent=2))
    typer.echo(json.dumps(to_dict(report), indent=2) if json_out else format_report(report))
    typer.echo(f"Wrote {out.resolve()}")


@app.command()
def viz(
    scenario: str = typer.Option(..., "--scenario", help="Scenario run to visualize."),
) -> None:
    """Render the run's board + per-person calendars to static HTML in runs/<scenario>/."""
    try:
        for out in (write_calendars(scenario), write_jira_tasks(scenario)):
            typer.echo(str(out.resolve()))
    except ConfigurationError as e:
        raise typer.BadParameter(
            f"{e.message}; create the run with `pm sim --scenario {scenario}`.",
            param_hint="--scenario") from e
    activity = write_agent_activity(scenario)
    typer.echo(str(activity.resolve()) if activity is not None
               else "no agent log; skipped agent_activity.html")


@app.command("log")
def log_cmd(
    scenario: str = typer.Option(..., "--scenario", help="Scenario run whose log to print."),
) -> None:
    """Print the run's sim-time timeline (the event_log) as narration lines."""
    path = _db_path(scenario)
    if not path.exists():
        raise typer.BadParameter(
            f"no run at {path}; create it with `pm sim --scenario {scenario}`.",
            param_hint="--scenario")
    store = Store.open(str(path))
    try:
        for entry in store.read_log():
            typer.echo(format_entry(entry))
    finally:
        store.close()


if __name__ == "__main__":
    app()
