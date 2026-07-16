"""AgentTools: the PM agent's send/read-Slack, read-board, read-calendar surface."""

from __future__ import annotations

import pytest

from pm.agent import AgentTools
from pm.env import Env
from pm.npc.cast import seed_cast
from pm.world.models import Meeting, Message, Project


@pytest.fixture
def env(tmp_path) -> Env:
    e = Env.make(run_id="agent", root=tmp_path)
    seed_cast(e.store)  # includes the agent "pm" plus the members/stakeholders
    e.store.add_project(Project(id="checkout", name="Checkout"))
    e.store.db.execute("INSERT INTO channel (id, name, kind) VALUES ('eng', 'eng', 'channel')")
    yield e
    e.close()


@pytest.fixture
def tools(env: Env) -> AgentTools:
    return AgentTools(env)


def test_send_slack_posts_costs_time_and_logs_action(tools: AgentTools, env: Env) -> None:
    assert env.clock.now() == 0
    posted = tools.send_slack("eng", "morning standup in 5")

    assert posted["sender_id"] == "pm" and posted["body"] == "morning standup in 5"
    assert env.clock.now() == tools.send_cost  # the send consumed sim-time
    assert posted["sent_tick"] == 1  # landed via the event pipeline, one tick later
    kinds = [(e.actor, e.kind) for e in env.store.read_log()]
    assert ("pm", "action") in kinds


def test_send_slack_triggers_a_slack_send_event(tools: AgentTools, env: Env) -> None:
    # The tool's mutation is an event: a slack.send row exists and completed.
    tools.send_slack("eng", "ping")
    row = env.store.db.query_one(
        "SELECT status, owner_id FROM event WHERE type = 'slack.send'")
    assert row is not None and row["status"] == "done" and row["owner_id"] == "pm"


def test_send_slack_unknown_channel_raises_tool_error(tools: AgentTools) -> None:
    from pm.exceptions import ToolError

    with pytest.raises(ToolError, match="unknown channel"):
        tools.send_slack("nope", "hello?")


def test_read_slack_returns_conversation(tools: AgentTools, env: Env) -> None:
    env.store.add_message(Message(id="eng-0", channel_id="eng", sender_id="alice",
                                  body="on it", sent_tick=0))
    tools.send_slack("eng", "thanks")
    convo = tools.read_slack("eng")
    assert [(m["sender_id"], m["body"]) for m in convo] == [
        ("alice", "on it"), ("pm", "thanks"),
    ]


def test_read_jira_board_summarizes_issues(tools: AgentTools) -> None:
    tools.jira.create_issue("checkout", "task", "API", estimate_minutes=60, actor="erin")
    tools.jira.create_issue("checkout", "task", "UI", estimate_minutes=60, actor="erin")
    board = tools.read_jira_board("checkout")

    assert board["project_id"] == "checkout"
    assert {i["title"] for i in board["issues"]} == {"API", "UI"}
    assert board["counts_by_status"] == {"todo": 2}


def test_read_calendar_returns_only_the_persons_meetings(tools: AgentTools, env: Env) -> None:
    env.store.add_meeting(Meeting(id="m1", title="Standup", start_tick=120, end_tick=150,
                                  attendees={"pm": "accepted", "alice": "accepted"}))
    env.store.add_meeting(Meeting(id="m2", title="Design sync", start_tick=200, end_tick=260,
                                  attendees={"alice": "accepted", "elieen": "accepted"}))

    mine = tools.read_calendar()  # defaults to the agent ("pm")
    assert [m["id"] for m in mine] == ["m1"]
    assert [m["id"] for m in tools.read_calendar("alice")] == ["m1", "m2"]
