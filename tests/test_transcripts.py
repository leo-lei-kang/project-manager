"""Authored standup transcripts: the .md content, availability gating, and the
PM's read tool.

The scenario hook under test: each tight_week standup persists a markdown
transcript when it ends, carrying the week-long thread about an off-board
request (live caption translation — Bob offers, Alice pushes back, the PM must
prioritize). The agent reviews them via ``AgentTools.read_transcripts``.
"""

from __future__ import annotations

import pytest

from pm.agent.tools import AgentTools
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.behavior import assignee_pickup_hook
from pm.scenarios import tight_week
from pm.sim.simulation import Simulation
from pm.transcript import STANDUP_DAYS, standup_transcript


def test_authored_transcripts_are_distinct_markdown():
    bodies = [standup_transcript(day) for day in range(STANDUP_DAYS)]
    assert len(set(bodies)) == STANDUP_DAYS
    assert all(b.startswith("# Daily standup") for b in bodies)
    with pytest.raises(ValueError, match="no standup transcript"):
        standup_transcript(5)


def test_monday_raises_the_off_board_conflict():
    monday = standup_transcript(0)
    assert "not on the GA board" in monday          # the new task isn't on Jira
    assert "live translation" in monday.lower()     # what it is
    assert "Bob:" in monday and "Alice:" in monday  # both positions on record
    assert "Needs the PM to prioritize" in monday   # the unresolved decision


def test_transcripts_become_available_as_standups_end(tmp_path):
    env = tight_week.build(run_id="tw", root=tmp_path)

    # Monday's standup runs 11:00-11:30 (ticks 120-150): nothing before it ends.
    env.engine.advance(149)
    assert env.store.list_transcripts(available_by=env.clock.now()) == []
    env.engine.advance(1)
    available = env.store.list_transcripts(available_by=env.clock.now())
    assert [t.meeting_id for t in available] == ["standup-0"]
    assert available[0].body == standup_transcript(0)

    Simulation(env).run()  # the rest of the week (meetings fire on the clock)
    transcripts = env.store.list_transcripts(available_by=env.clock.now())
    assert len(transcripts) == 11  # every meeting leaves one (empty body by default)
    standups = {t.meeting_id: t.body for t in transcripts if t.meeting_id.startswith("standup")}
    assert standups == {f"standup-{d}": standup_transcript(d) for d in range(STANDUP_DAYS)}
    env.close()


def test_every_meeting_leaves_a_transcript_by_default(tmp_path):
    # The 1:1s carry no transcript_id/body in their payload; they still leave an
    # (empty) transcript when they end — Monday's 1:1 runs 14:00-14:30.
    env = tight_week.build(run_id="tw-default", root=tmp_path)
    env.engine.advance(330)  # Mon 14:30
    one_on_one = [t for t in env.store.list_transcripts(available_by=env.clock.now())
                  if t.meeting_id.startswith("1on1")]
    assert len(one_on_one) == 1
    assert one_on_one[0].body == ""
    env.close()


def test_agent_reads_transcripts_with_meeting_context(tmp_path):
    env = tight_week.build(run_id="tw-agent", root=tmp_path)
    tools = AgentTools(env)
    assert tools.read_transcripts() == []  # nothing has happened yet

    api = JiraApi(JiraRepository(env.store), env.engine)
    Simulation(env).run(
        on_tick=assignee_pickup_hook(api, tight_week.MEMBERS, tight_week.PROJECT_ID))

    seen = tools.read_transcripts()
    assert len(seen) == 11  # every meeting leaves a transcript now
    monday = next(t for t in seen if t["meeting_id"] == "standup-0")
    assert monday["title"] == "Daily standup"
    assert monday["available_tick"] == 150  # Mon 11:30
    assert "Needs the PM to prioritize" in monday["body"]
    env.close()
