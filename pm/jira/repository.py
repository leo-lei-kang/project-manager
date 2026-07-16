"""Persistence for Jira issues — an additive repository over the existing DB.

``JiraRepository`` reuses the project's ``Store``/``Database`` connection but owns
its own tables (``issue``, ``issue_dependency``). It applies its DDL idempotently
(``CREATE TABLE IF NOT EXISTS``) without editing the shared ``schema.sql`` or
bumping ``SCHEMA_VERSION`` — so it can be added to a live database created by the
core schema. The read/write pattern is a transactional insert + child edge table
(``issue_dependency``) + dependency re-read.

Raw SQL stays inside this class; callers get typed :class:`Issue` objects.
"""

from __future__ import annotations

from pm.db.store import Store
from pm.jira.models import Issue, Rollup

# Statuses that count as "no longer blocking" for dependency purposes.
TERMINAL_STATUSES = ("done", "cancelled")

_DDL = """
CREATE TABLE IF NOT EXISTS issue (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    issue_type        TEXT NOT NULL CHECK (issue_type IN ('epic', 'story', 'task')),
    parent_id         TEXT REFERENCES issue(id) ON DELETE SET NULL,
    title             TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'todo'
                      CHECK (status IN ('todo','in_progress','blocked','in_review','done','cancelled')),
    assignee_id       TEXT REFERENCES person(id) ON DELETE SET NULL,
    component         TEXT NOT NULL DEFAULT '',
    priority          INTEGER NOT NULL DEFAULT 2,
    estimate_minutes  INTEGER NOT NULL DEFAULT 0,
    remaining_minutes INTEGER NOT NULL DEFAULT 0,
    created_tick      INTEGER NOT NULL DEFAULT 0,
    updated_tick      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_issue_project ON issue (project_id);
CREATE INDEX IF NOT EXISTS idx_issue_parent  ON issue (parent_id);

CREATE TABLE IF NOT EXISTS issue_dependency (
    issue_id            TEXT NOT NULL REFERENCES issue(id) ON DELETE CASCADE,
    depends_on_issue_id TEXT NOT NULL REFERENCES issue(id) ON DELETE CASCADE,
    PRIMARY KEY (issue_id, depends_on_issue_id),
    CHECK (issue_id <> depends_on_issue_id)
);
"""

# Columns `update_fields` is allowed to write (plus updated_tick, always set).
_UPDATABLE = frozenset(
    {
        "title",
        "description",
        "status",
        "assignee_id",
        "component",
        "parent_id",
        "priority",
        "estimate_minutes",
        "remaining_minutes",
    }
)


class JiraRepository:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.db = store.db

    def ensure_schema(self) -> None:
        """Create the issue tables if absent. Idempotent; safe on a live DB."""
        with self.db.transaction() as conn:
            conn.executescript(_DDL)

    # -- writes --------------------------------------------------------------

    def add_issue(self, issue: Issue) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO issue (id, project_id, issue_type, parent_id, title, "
                "description, status, assignee_id, component, priority, estimate_minutes, "
                "remaining_minutes, created_tick, updated_tick) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    issue.id,
                    issue.project_id,
                    issue.issue_type,
                    issue.parent_id,
                    issue.title,
                    issue.description,
                    issue.status,
                    issue.assignee_id,
                    issue.component,
                    issue.priority,
                    issue.estimate_minutes,
                    issue.remaining_minutes,
                    issue.created_tick,
                    issue.updated_tick,
                ),
            )
            for dep in issue.depends_on:
                conn.execute(
                    "INSERT OR IGNORE INTO issue_dependency "
                    "(issue_id, depends_on_issue_id) VALUES (?, ?)",
                    (issue.id, dep),
                )

    def update_fields(self, key: str, tick: int, **fields: object) -> None:
        cols = [c for c in fields if c in _UPDATABLE]
        assignments = ", ".join(f"{c} = ?" for c in cols) + ", updated_tick = ?"
        params = [fields[c] for c in cols] + [tick, key]
        self.db.execute(f"UPDATE issue SET {assignments} WHERE id = ?", params)

    def set_status(self, key: str, status: str, tick: int) -> None:
        self.update_fields(key, tick, status=status)

    def set_remaining(self, key: str, minutes: int, tick: int) -> None:
        self.update_fields(key, tick, remaining_minutes=minutes)

    def set_assignee(self, key: str, assignee_id: str | None, tick: int) -> None:
        self.update_fields(key, tick, assignee_id=assignee_id)

    def add_dependency(self, key: str, depends_on_key: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO issue_dependency (issue_id, depends_on_issue_id) "
            "VALUES (?, ?)",
            (key, depends_on_key),
        )

    def remove_dependency(self, key: str, depends_on_key: str) -> None:
        self.db.execute(
            "DELETE FROM issue_dependency WHERE issue_id = ? AND depends_on_issue_id = ?",
            (key, depends_on_key),
        )

    # -- reads ---------------------------------------------------------------

    def get_issue(self, key: str) -> Issue | None:
        r = self.db.query_one("SELECT * FROM issue WHERE id = ?", (key,))
        return self._row_to_issue(r) if r else None

    def list_issues(
        self,
        *,
        project_id: str | None = None,
        issue_type: str | None = None,
        parent_id: str | None = None,
        status: str | None = None,
        assignee_id: str | None = None,
        component: str | None = None,
    ) -> list[Issue]:
        clauses: list[str] = []
        params: list[object] = []
        for col, val in (
            ("project_id", project_id),
            ("issue_type", issue_type),
            ("parent_id", parent_id),
            ("status", status),
            ("assignee_id", assignee_id),
            ("component", component),
        ):
            if val is not None:
                clauses.append(f"{col} = ?")
                params.append(val)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query_all(
            f"SELECT * FROM issue{where} ORDER BY priority, id", params
        )
        return [self._row_to_issue(r) for r in rows]

    def children(self, parent_key: str) -> list[Issue]:
        return self.list_issues(parent_id=parent_key)

    def dependents(self, key: str) -> list[Issue]:
        """Issues that depend on ``key`` (i.e. ``key`` is in their depends_on)."""
        rows = self.db.query_all(
            "SELECT issue_id FROM issue_dependency WHERE depends_on_issue_id = ? "
            "ORDER BY issue_id",
            (key,),
        )
        return [i for i in (self.get_issue(r["issue_id"]) for r in rows) if i]

    def subtree(self, key: str) -> list[Issue]:
        """The issue and all its descendants, via a recursive parent-walk."""
        rows = self.db.query_all(
            "WITH RECURSIVE sub(id) AS ("
            "  SELECT id FROM issue WHERE id = ?"
            "  UNION ALL"
            "  SELECT i.id FROM issue i JOIN sub ON i.parent_id = sub.id"
            ") SELECT id FROM sub",
            (key,),
        )
        return [i for i in (self.get_issue(r["id"]) for r in rows) if i]

    def rollup(self, key: str) -> Rollup:
        nodes = self.subtree(key)
        root = self.get_issue(key)
        child_of = {n.parent_id for n in nodes if n.parent_id is not None}
        by_status: dict[str, int] = {}
        for n in nodes:
            by_status[n.status] = by_status.get(n.status, 0) + 1
        return Rollup(
            key=key,
            issue_type=root.issue_type if root else "",
            estimate_minutes=sum(n.estimate_minutes for n in nodes),
            remaining_minutes=sum(n.remaining_minutes for n in nodes),
            by_status=by_status,
            leaf_count=sum(1 for n in nodes if n.id not in child_of),
        )

    def next_key(self, prefix: str) -> str:
        """Next Jira-style key ``<PREFIX>-<n>`` for a project prefix."""
        rows = self.db.query_all(
            "SELECT id FROM issue WHERE id LIKE ?", (f"{prefix}-%",)
        )
        highest = 0
        for r in rows:
            _, _, suffix = r["id"].partition("-")
            if suffix.isdigit():
                highest = max(highest, int(suffix))
        return f"{prefix}-{highest + 1}"

    # -- mapping -------------------------------------------------------------

    def _row_to_issue(self, r) -> Issue:
        deps = [
            row["depends_on_issue_id"]
            for row in self.db.query_all(
                "SELECT depends_on_issue_id FROM issue_dependency WHERE issue_id = ? "
                "ORDER BY depends_on_issue_id",
                (r["id"],),
            )
        ]
        return Issue(
            id=r["id"],
            project_id=r["project_id"],
            issue_type=r["issue_type"],
            parent_id=r["parent_id"],
            title=r["title"],
            description=r["description"],
            status=r["status"],
            assignee_id=r["assignee_id"],
            component=r["component"],
            priority=r["priority"],
            estimate_minutes=r["estimate_minutes"],
            remaining_minutes=r["remaining_minutes"],
            depends_on=deps,
            created_tick=r["created_tick"],
            updated_tick=r["updated_tick"],
        )
