"""Every registered scenario, built with no persona override, matches scenarios.md.

Each scenario is built at its baked-in persona and driven exactly like `pm sim`
(`pm.scenarios.runner.drive`); the eval outcome — plus the launch-blocker subset for
the solo boards — is asserted against the documented catalog. A separate end-to-end
test confirms the CLI writes the full result bundle to `runs/<scenario>/`.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from pm.cli import SCENARIOS, app
from pm.eval import evaluate
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.scenarios import runner
from pm.viz import write_calendars, write_jira_tasks

from tests.llm_fakes import FakeClient, text_resp

_HIGH_PRIORITY = 1

# name -> (done_tasks, total_tasks, goal_accomplished, launch_blockers_left)
# blockers_left is None for the non-solo boards. Verified at seed 42 (scenarios.md).
EXPECTED = {
    "team_no_jira":                          (15, 25, False, None),
    "team_no_jira_with_agent":               (15, 25, False, None),
    "test_single_engineer_free_spirit":      (6, 11, False, 2),
    "test_single_engineer_with_agent":       (6, 11, False, 2),
    "test_two_engineers_mixed":              (8, 16, False, None),
    "test_two_engineers_mixed_with_agent":   (8, 16, False, None),
}


def test_every_registered_scenario_has_an_expected_row() -> None:
    # A newly registered scenario must declare its documented outcome here.
    assert set(SCENARIOS) == set(EXPECTED)


@pytest.mark.parametrize("name", list(EXPECTED))
def test_scenario_outcome_matches_catalog(name: str, tmp_path, monkeypatch) -> None:
    module = SCENARIOS[name]
    if hasattr(module, "agent_review_hook"):
        # Keep the run hermetic: an LLM PM that says nothing steers nothing, so
        # the outcome equals the unmanaged row it documents.
        hook = module.agent_review_hook
        monkeypatch.setattr(
            module, "agent_review_hook",
            lambda env: hook(env, client=FakeClient([text_resp("nothing to do")])))
    env = module.build(run_id=name, root=tmp_path)  # baked persona, no override
    runner.drive(env, module)

    report = evaluate(env.store, project_id=module.PROJECT_ID)
    done, total, goal, blockers_left = EXPECTED[name]
    assert (report.done_tasks, report.total_tasks, report.goal_accomplished) == (done, total, goal)

    if blockers_left is not None:  # solo boards: the 7 launch blockers are priority 1
        api = JiraApi(JiraRepository(env.store), env.engine)
        tasks = api.search(project_id=module.PROJECT_ID, issue_type="task")
        left = sum(1 for t in tasks if t.priority == _HIGH_PRIORITY and t.status != "done")
        assert left == blockers_left

    env.close()
    # viz renders for every scenario, into the run folder
    assert write_calendars(name, root=tmp_path).exists()
    assert write_jira_tasks(name, root=tmp_path).exists()


def test_cli_writes_full_bundle_to_run_folder(tmp_path, monkeypatch) -> None:
    # sim + eval + viz over the scenario-only CLI leave a self-contained runs/<name>/.
    monkeypatch.chdir(tmp_path)
    runner_cli = CliRunner()
    name = "test_single_engineer_free_spirit"
    for args in (["sim", "--scenario", name],
                 ["eval", "--scenario", name],
                 ["viz", "--scenario", name]):
        result = runner_cli.invoke(app, args)
        assert result.exit_code == 0, (args, result.output)

    run = tmp_path / "runs" / name
    for artifact in ("world.db", "seed.db", "eval.json", "calendars.html", "jira_tasks.html"):
        assert (run / artifact).exists(), artifact
