"""Unified sim logging: on_log streaming, narration lines, and the logged kinds."""

from __future__ import annotations

from typer.testing import CliRunner

from pm.cli import app
from pm.db.store import Store
from pm.scenarios import runner, team_mixed_persona
from pm.scenarios.test_single_engineer_with_agent import PROJECT_ID, agent_review_hook
from pm.scenarios.test_single_engineer_with_agent import build as build_agent_scenario
from pm.sim.clock import WEEK_END_TICK
from pm.sim.narrate import format_entry
from pm.sim.simulation import Simulation
from pm.world.models import LogEntry

from tests.llm_fakes import FakeClient, text_resp, tool_call_resp


def test_store_on_log_streams_each_entry(tmp_path):
    store = Store.open(str(tmp_path / "w.db"), create=True)
    seen: list[LogEntry] = []
    store.on_log = seen.append
    store.log_event(5, actor="alice", kind="npc.pickup", payload={"issue_key": "GA-1"})
    store.close()
    assert [(e.sim_tick, e.actor, e.kind, e.payload) for e in seen] == [
        (5, "alice", "npc.pickup", {"issue_key": "GA-1"})]


def test_format_entry_renders_sim_time_and_detail():
    line = format_entry(LogEntry(
        sim_tick=120, actor="alice", kind="npc.pickup", payload={"issue_key": "GA-1"}))
    assert "Mon 11:00" in line and "alice" in line
    assert "npc.pickup" in line and "GA-1" in line

    agent = format_entry(LogEntry(
        sim_tick=0, actor="pm", kind="agent.llm_call",
        payload={"model": "m/1", "input_tokens": 10, "output_tokens": 2}))
    assert "m/1" in agent and "10 in / 2 out tokens" in agent


def test_week_logs_npc_decisions_and_activity_transitions(tmp_path):
    # Mixed-persona team week: free spirits pick, standups interrupt work and
    # close heads-down clare's in_review tickets — every gap kind must appear.
    env = team_mixed_persona.build(run_id="log-week", root=tmp_path)
    runner.drive(env, team_mixed_persona)

    entries = env.store.read_log()
    kinds = {e.kind for e in entries}
    assert {"npc.pickup", "npc.close_review", "event.scheduled", "activity.start",
            "activity.interrupt", "activity.resume", "activity.done"} <= kinds
    assert all(0 <= e.sim_tick <= WEEK_END_TICK for e in entries)  # all sim-time stamped
    pickup = next(e for e in entries if e.kind == "npc.pickup")
    assert pickup.payload["issue_key"] and pickup.actor
    close = next(e for e in entries if e.kind == "npc.close_review")
    assert close.payload["trigger"] == "standup"
    env.close()


def test_agent_actions_mirror_into_event_log(tmp_path):
    env = build_agent_scenario(run_id="log-agent", root=tmp_path)
    hook = agent_review_hook(env, client=FakeClient([
        tool_call_resp("read_jira_board", {"project_id": PROJECT_ID}),
        text_resp("board reviewed"),
    ]))
    hook(Simulation(env))  # tick 0 is a review boundary

    agent_rows = [e for e in env.store.read_log() if e.kind.startswith("agent.")]
    assert [e.kind for e in agent_rows] == [
        "agent.llm_call", "agent.tool_call", "agent.llm_call"]
    assert all(e.actor == "pm" and e.sim_tick == 0 for e in agent_rows)
    assert agent_rows[0].payload["input_tokens"] > 0  # usage carried into the mirror
    env.close()


def test_cli_streams_by_default_silences_with_no_verbose_and_prints_log(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli = CliRunner()

    loud = cli.invoke(app, ["sim", "--scenario", "test_two_engineers"])
    assert loud.exit_code == 0, loud.output
    assert "npc.pickup" in loud.output  # streamed while simulating

    quiet = cli.invoke(app, ["sim", "--scenario", "test_single_engineer", "--no-verbose"])
    assert quiet.exit_code == 0, quiet.output
    assert "npc.pickup" not in quiet.output

    log = cli.invoke(app, ["log", "--scenario", "test_single_engineer"])
    assert log.exit_code == 0, log.output
    assert "npc.pickup" in log.output and "Mon 09:00" in log.output
