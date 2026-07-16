"""Authored meeting transcripts: the .md content, availability gating, and the
PM's read tool.

The authored set is team_no_jira's week (``pm/transcript/no-jira-*.md``):
Monday's kickoff, then Tue-Fri standups. Every meeting persists a transcript
when it ends — meetings without authored notes leave an empty one — and the
agent reviews them via ``AgentTools.read_transcripts``.
"""

from __future__ import annotations

import pytest

from pm.agent.tools import AgentTools
from pm.scenarios import runner, team_no_jira, team_with_jira
from pm.transcript import STANDUP_DAYS, project_brief, standup_transcript


def test_authored_transcripts_are_distinct_markdown():
    bodies = [standup_transcript(day) for day in range(STANDUP_DAYS)]
    assert len(set(bodies)) == STANDUP_DAYS
    assert bodies[0].startswith("# Project kickoff")
    assert all(b.startswith("# Daily standup") for b in bodies[1:])
    with pytest.raises(ValueError, match="no standup transcript"):
        standup_transcript(5)


def test_transcripts_become_available_as_meetings_end(tmp_path):
    env = team_no_jira.build(run_id="nj-avail", root=tmp_path)

    # Monday's kickoff runs 11:00-12:00 (ticks 120-180): nothing before it ends.
    env.engine.advance(179)
    assert env.store.list_transcripts(available_by=env.clock.now()) == []
    env.engine.advance(1)
    available = env.store.list_transcripts(available_by=env.clock.now())
    assert [t.meeting_id for t in available] == ["no-jira-0"]
    assert available[0].body == standup_transcript(0) + "\n" + project_brief()
    env.close()


def test_every_meeting_leaves_a_transcript_by_default(tmp_path):
    # The 1:1s carry no transcript_id/body in their payload; they still leave an
    # (empty) transcript when they end — Monday's 1:1 runs 14:00-14:30.
    env = team_with_jira.build(run_id="tw-default", root=tmp_path)
    env.engine.advance(330)  # Mon 14:30
    one_on_one = [t for t in env.store.list_transcripts(available_by=env.clock.now())
                  if t.meeting_id.startswith("1on1")]
    assert len(one_on_one) == 1
    assert one_on_one[0].body == ""
    env.close()


def test_agent_reads_transcripts_with_meeting_context(tmp_path):
    env = team_with_jira.build(run_id="tw-agent", root=tmp_path)
    tools = AgentTools(env)
    assert tools.read_transcripts() == []  # nothing has happened yet

    runner.drive(env, team_with_jira)

    seen = tools.read_transcripts()
    assert len(seen) == 11  # every meeting leaves a transcript
    monday = next(t for t in seen if t["meeting_id"] == "standup-0")
    assert monday["title"] == "Daily standup"
    assert monday["available_tick"] == 150  # Mon 11:30
    assert monday["body"] == ""  # team_with_jira meetings carry no authored notes
    env.close()
