"""Jira-style work-item model.

A single :class:`Issue` entity with an ``issue_type`` discriminator (epic / story
/ task) and a ``parent_id`` link — mirroring how real Jira models one issue type
with an issuetype field, rather than three separate classes. Each issue carries
its own ``estimate_minutes`` ("how many minutes it needs"); parents additionally
expose a *derived* rollup (see :class:`Rollup` and ``JiraRepository.rollup``).

Conventions match ``pm/world/models.py``: Pydantic v2 ``BaseModel``, ``Literal``
string enums (so they round-trip through SQLite with no adapters), caller-visible
string ids — here the id is a Jira-style key, e.g. ``"CHECKOUT-1"``. Time fields
are integer sim ticks, and by the clock's convention one tick == one minute, so
``estimate_minutes`` maps 1:1 onto sim-time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

IssueType = Literal["epic", "story", "task"]
IssueStatus = Literal[
    "todo", "in_progress", "blocked", "in_review", "done", "cancelled"
]


class Issue(BaseModel):
    id: str  # Jira key, e.g. "CHECKOUT-1"
    project_id: str
    issue_type: IssueType
    parent_id: str | None = None  # Epic <- Story <- Task hierarchy link
    title: str  # Jira "summary"
    description: str = ""
    status: IssueStatus = "todo"
    assignee_id: str | None = None
    component: str = ""  # discipline that owns the work, e.g. backend/frontend/design
    priority: int = 2
    estimate_minutes: int = 0  # how many minutes this item needs
    remaining_minutes: int = 0  # set to estimate at creation; decremented by log_work
    depends_on: list[str] = Field(default_factory=list)  # keys this issue is blocked by
    created_tick: int = 0
    updated_tick: int = 0


@dataclass
class Rollup:
    """Derived aggregate over an issue's subtree ({issue} ∪ descendants).

    Never stored — computed on demand from the tree, so it cannot drift from the
    underlying issues or be gamed by writing a rolled-up number directly.
    """

    key: str
    issue_type: str
    estimate_minutes: int
    remaining_minutes: int
    by_status: dict[str, int]
    leaf_count: int
