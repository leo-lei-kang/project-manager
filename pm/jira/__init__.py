"""Jira-style issue tracking as an additive module over the pm simulation.

A single :class:`Issue` model (epic / story / task via ``issue_type``) with a
parent link, per-issue ``estimate_minutes``, and dependency links; a
:class:`JiraRepository` owning its own tables on the shared SQLite DB; and a
:class:`JiraApi` that mimics Jira's REST surface and routes every mutation
through the simulation engine.
"""

from pm.jira.api import JiraApi
from pm.jira.models import Issue, IssueStatus, IssueType, Rollup
from pm.jira.repository import JiraRepository

__all__ = [
    "Issue",
    "IssueStatus",
    "IssueType",
    "Rollup",
    "JiraRepository",
    "JiraApi",
]
