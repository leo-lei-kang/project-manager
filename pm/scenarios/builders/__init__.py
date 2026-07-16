"""Per-event scenario builders.

One helper class per event type (each ``build()`` emits a single event kind), a
shared :class:`~pm.scenarios.builders.base.BuildContext` / :class:`Level`, and the
default ordered ``BUILDERS`` list the generator drives.
"""

from __future__ import annotations

from pm.scenarios.builders.base import (
    LEVEL_ORDER,
    BuildContext,
    EventBuilder,
    Level,
    at,
    spread,
)
from pm.scenarios.builders.chat import ChatBuilder
from pm.scenarios.builders.email import EmailBuilder
from pm.scenarios.builders.jira_task import JiraTaskBuilder
from pm.scenarios.builders.meeting import MeetingBuilder
from pm.scenarios.builders.ooo import OOOBuilder

# Core-set builders. OOO first so its calendar occupancy is reserved before
# meetings/work for the same person, letting the calendar shift those around it.
BUILDERS: list[EventBuilder] = [
    OOOBuilder(),
    JiraTaskBuilder(),
    MeetingBuilder(),
    EmailBuilder(),
    ChatBuilder(),
]

__all__ = [
    "Level",
    "LEVEL_ORDER",
    "BuildContext",
    "EventBuilder",
    "at",
    "spread",
    "JiraTaskBuilder",
    "MeetingBuilder",
    "EmailBuilder",
    "ChatBuilder",
    "OOOBuilder",
    "BUILDERS",
]
