"""Jira-mimicking API surface over :class:`JiraRepository`.

``JiraApi`` is the agent-facing tool layer. Its method names and semantics track
Jira's REST resources (create / get / search / transition / assign / issueLink /
worklog / rollup). Every **mutation routes through** ``Engine.perform_action`` so
the single-writer rule and the action log hold; ``action_cost`` is the sim-time a
Jira interaction costs (default 0 — the issue's ``estimate_minutes`` is the real
*work*, not the cost of clicking around the board).

Validation the API enforces (Jira-shaped ``ToolError`` payloads on failure):
  * issue-type hierarchy: epics are top-level; stories sit under epics; tasks
    under stories or epics;
  * dependency links reject self-links and cycles;
  * status transitions are checked against an allowed-transition map;
  * ``blocked`` is *derived*: after any status/link change the affected issues'
    todo/blocked state is recomputed from whether their dependencies are done.
"""

from __future__ import annotations

import re

from pm.exceptions import ToolError
from pm.jira.models import Issue, IssueStatus, IssueType, Rollup
from pm.jira.repository import TERMINAL_STATUSES, JiraRepository
from pm.sim.engine import Engine

# Allowed manual status transitions (Jira-style workflow). "blocked" is not a
# manual target — it is applied only by the derived recompute below.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "todo": {"in_progress", "cancelled"},
    "in_progress": {"in_review", "done", "todo", "cancelled"},
    "in_review": {"done", "in_progress", "cancelled"},
    "blocked": {"in_progress", "todo", "cancelled"},
    "done": {"in_progress"},
    "cancelled": {"todo"},
}

# Statuses the derived-blocked recompute is allowed to toggle between.
_MANAGED = {"todo", "blocked"}

# Which parent issue types are legal for each issue type (None is always allowed
# except for epics, which must be top-level).
_PARENT_RULES: dict[str, tuple[str, ...]] = {
    "epic": (),
    "story": ("epic",),
    "task": ("epic", "story"),
}


def _prefix_from(project_id: str) -> str:
    head = project_id.split("-")[0]
    cleaned = re.sub(r"[^A-Za-z0-9]", "", head).upper()
    return cleaned or "ISSUE"


class JiraApi:
    def __init__(
        self, repo: JiraRepository, engine: Engine, *, action_cost: int = 0
    ) -> None:
        self.repo = repo
        self.engine = engine
        self.action_cost = action_cost

    # -- helpers -------------------------------------------------------------

    def _tick(self) -> int:
        return self.engine.clock.now()

    @staticmethod
    def _error(message: str, status: int, **errors: str) -> ToolError:
        # Jira-shaped error body: errorMessages + per-field errors + HTTP-ish status.
        return ToolError(
            message,
            details={"errorMessages": [message], "errors": errors, "status": status},
        )

    def _require(self, key: str) -> Issue:
        issue = self.repo.get_issue(key)
        if issue is None:
            raise self._error(f"issue {key!r} does not exist", 404, issueKey="not found")
        return issue

    def _mutate(self, actor: str, effect) -> None:
        self.engine.perform_action(actor=actor, cost=self.action_cost, effect=effect)

    # -- create / read -------------------------------------------------------

    def create_issue(
        self,
        project_id: str,
        issue_type: IssueType,
        summary: str,
        *,
        parent: str | None = None,
        estimate_minutes: int = 0,
        assignee: str | None = None,
        component: str = "",
        priority: int = 2,
        description: str = "",
        depends_on: list[str] | None = None,
        key_prefix: str | None = None,
        actor: str = "agent",
    ) -> Issue:
        if self.repo.store.get_project(project_id) is None:
            raise self._error(f"project {project_id!r} not found", 400, project="unknown")
        if estimate_minutes < 0:
            raise self._error("estimate_minutes must be >= 0", 400, estimate="negative")
        self._validate_parent(issue_type, parent)
        if assignee is not None and self.repo.store.get_person(assignee) is None:
            raise self._error(f"assignee {assignee!r} not found", 400, assignee="unknown")

        deps = list(depends_on or [])
        for dep in deps:
            if self.repo.get_issue(dep) is None:
                raise self._error(f"dependency {dep!r} not found", 400, depends_on="unknown")

        key = self.repo.next_key(key_prefix or _prefix_from(project_id))
        tick = self._tick()
        issue = Issue(
            id=key,
            project_id=project_id,
            issue_type=issue_type,
            parent_id=parent,
            title=summary,
            description=description,
            status="todo",
            assignee_id=assignee,
            component=component,
            priority=priority,
            estimate_minutes=estimate_minutes,
            remaining_minutes=estimate_minutes,  # new issues start with full remaining
            depends_on=deps,
            created_tick=tick,
            updated_tick=tick,
        )
        self._mutate(actor, lambda: self.repo.add_issue(issue))
        self.recompute_blocked(key)
        return self._require(key)

    def get_issue(self, key: str) -> Issue:
        return self._require(key)

    def search(
        self,
        *,
        project_id: str | None = None,
        issue_type: str | None = None,
        status: str | None = None,
        assignee: str | None = None,
        parent: str | None = None,
        component: str | None = None,
    ) -> list[Issue]:
        return self.repo.list_issues(
            project_id=project_id,
            issue_type=issue_type,
            parent_id=parent,
            status=status,
            assignee_id=assignee,
            component=component,
        )

    def get_rollup(self, key: str) -> Rollup:
        self._require(key)
        return self.repo.rollup(key)

    # -- transitions / edits -------------------------------------------------

    def transition_issue(self, key: str, to_status: IssueStatus, actor: str = "agent") -> Issue:
        issue = self._require(key)
        if to_status == issue.status:
            return issue
        allowed = ALLOWED_TRANSITIONS.get(issue.status, set())
        if to_status not in allowed:
            raise self._error(
                f"illegal transition {issue.status!r} -> {to_status!r}",
                409,
                transition=f"allowed from {issue.status!r}: {sorted(allowed)}",
            )
        tick = self._tick()
        self._mutate(actor, lambda: self.repo.set_status(key, to_status, tick))
        # A completed/cancelled blocker may unblock its dependents; a reopened one
        # may re-block them. Recompute the dependents, then this issue itself.
        self._recompute_dependents(key)
        self.recompute_blocked(key)
        return self._require(key)

    def assign_issue(self, key: str, assignee_id: str | None, actor: str = "agent") -> Issue:
        self._require(key)
        if assignee_id is not None and self.repo.store.get_person(assignee_id) is None:
            raise self._error(f"assignee {assignee_id!r} not found", 400, assignee="unknown")
        tick = self._tick()
        self._mutate(actor, lambda: self.repo.set_assignee(key, assignee_id, tick))
        return self._require(key)

    def set_estimate(self, key: str, minutes: int, actor: str = "agent") -> Issue:
        self._require(key)
        if minutes < 0:
            raise self._error("estimate must be >= 0", 400, estimate="negative")
        tick = self._tick()
        self._mutate(actor, lambda: self.repo.update_fields(key, tick, estimate_minutes=minutes))
        return self._require(key)

    def log_work(self, key: str, minutes: int, actor: str = "agent") -> Issue:
        issue = self._require(key)
        if minutes < 0:
            raise self._error("worklog minutes must be >= 0", 400, minutes="negative")
        new_remaining = max(0, issue.remaining_minutes - minutes)
        tick = self._tick()
        self._mutate(actor, lambda: self.repo.set_remaining(key, new_remaining, tick))
        return self._require(key)

    def link_issue(
        self,
        inward_key: str,
        outward_key: str,
        *,
        link_type: str = "blocks",
        actor: str = "agent",
    ) -> None:
        """Record a dependency: *outward blocks inward* ⇒ inward depends on outward."""
        if link_type != "blocks":
            raise self._error(
                f"unsupported link type {link_type!r}", 400, type="only 'blocks' supported"
            )
        self._require(inward_key)
        self._require(outward_key)
        if inward_key == outward_key:
            raise self._error("an issue cannot block itself", 400, link="self-link")
        if self._would_cycle(inward_key, outward_key):
            raise self._error(
                "link would create a dependency cycle", 400, link="cycle"
            )
        self._mutate(actor, lambda: self.repo.add_dependency(inward_key, outward_key))
        self.recompute_blocked(inward_key)

    def unlink_issue(self, inward_key: str, outward_key: str, actor: str = "agent") -> None:
        self._require(inward_key)
        self._mutate(actor, lambda: self.repo.remove_dependency(inward_key, outward_key))
        self.recompute_blocked(inward_key)

    # -- derived blocked state ----------------------------------------------

    def recompute_blocked(self, key: str) -> None:
        """Toggle an unstarted issue between todo and blocked from its deps.

        Localizes (to this module) the auto-blocking the core schema documents
        but never implements. Only touches issues in a *managed* state
        (todo/blocked); work already in progress or finished is left alone.
        """
        issue = self.repo.get_issue(key)
        if issue is None or issue.status not in _MANAGED:
            return
        blocked = any(self._dependency_open(dep) for dep in issue.depends_on)
        target = "blocked" if blocked else "todo"
        if target != issue.status:
            self.repo.set_status(key, target, self._tick())

    def _recompute_dependents(self, key: str) -> None:
        for dependent in self.repo.dependents(key):
            self.recompute_blocked(dependent.id)

    def _dependency_open(self, dep_key: str) -> bool:
        dep = self.repo.get_issue(dep_key)
        return dep is not None and dep.status not in TERMINAL_STATUSES

    # -- validation ----------------------------------------------------------

    def _validate_parent(self, issue_type: str, parent: str | None) -> None:
        if parent is None:
            return  # top-level is legal for every type
        if issue_type == "epic":
            raise self._error("an epic cannot have a parent", 400, parent="epics are top-level")
        p = self.repo.get_issue(parent)
        if p is None:
            raise self._error(f"parent {parent!r} not found", 400, parent="unknown issue")
        allowed = _PARENT_RULES[issue_type]
        if p.issue_type not in allowed:
            raise self._error(
                f"a {issue_type} cannot be a child of a {p.issue_type}",
                400,
                parent=f"must be one of {list(allowed)}",
            )

    def _would_cycle(self, inward: str, outward: str) -> bool:
        """Would adding 'inward depends_on outward' create a cycle?

        A cycle forms iff ``outward`` already (transitively) depends on
        ``inward``. Walk the depends_on graph from ``outward``.
        """
        stack = [outward]
        seen: set[str] = set()
        while stack:
            cur = stack.pop()
            if cur == inward:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            issue = self.repo.get_issue(cur)
            if issue is not None:
                stack.extend(issue.depends_on)
        return False
