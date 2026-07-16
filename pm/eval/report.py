"""Evaluate a run's Jira outcomes from its ``world.db`` — read-only.

The first slice of the evaluator: a deterministic report over the final board
state. It answers, for one project: how many hours of task work were completed,
whether the week goal was accomplished, who accomplished what, and what remains.

Only leaf ``task`` issues are counted (stories/epics are derived rollups over
them, so counting all types would double-count). A done task's ``updated_tick``
is its completion tick — the ``done`` transition is the last write it receives.
Completed hours are the sum of done tasks' ``estimate_minutes``: remaining work
is logged out at completion, so a done task's estimate equals the effort spent.

The **week goal is accomplished** when every task in the project is ``done`` and
the last completion tick is at or before the project's ``deadline_tick``
(falling back to the end of the work week when no deadline is set).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pm.db.store import Store
from pm.exceptions import ConfigurationError
from pm.jira.format import format_hours
from pm.jira.models import Issue
from pm.jira.repository import JiraRepository
from pm.sim.clock import WEEK_END_TICK, format_tick


@dataclass
class PersonReport:
    """One person's slice of the board: what they finished and what they still hold."""

    person_id: str
    name: str
    done: list[Issue] = field(default_factory=list)
    done_minutes: int = 0
    remaining: list[Issue] = field(default_factory=list)
    remaining_minutes: int = 0


@dataclass
class EvalReport:
    """The evaluation of one project's board at the moment the store was read."""

    project_id: str
    project_name: str
    deadline_tick: int
    total_tasks: int
    done_tasks: int
    total_minutes: int
    done_minutes: int
    last_done_tick: int | None
    goal_accomplished: bool
    people: list[PersonReport]
    remaining: list[Issue]


def _resolve_project_id(store: Store, project_id: str | None) -> str:
    """The project to evaluate: the given one, or the run's single project."""
    if project_id is not None:
        if store.get_project(project_id) is None:
            raise ConfigurationError(
                f"project {project_id!r} not found", details={"project_id": project_id}
            )
        return project_id
    rows = store.db.query_all("SELECT id FROM project ORDER BY id")
    ids = [str(r["id"]) for r in rows]
    if len(ids) != 1:
        raise ConfigurationError(
            "run has no single project; pass project_id explicitly",
            details={"projects": ids},
        )
    return ids[0]


def evaluate(store: Store, project_id: str | None = None) -> EvalReport:
    """Evaluate ``project_id``'s board (or the run's only project) from ``store``."""
    pid = _resolve_project_id(store, project_id)
    project = store.get_project(pid)
    assert project is not None  # _resolve_project_id verified existence
    tasks = JiraRepository(store).list_issues(project_id=pid, issue_type="task")

    done = [t for t in tasks if t.status == "done"]
    remaining = [t for t in tasks if t.status != "done"]
    last_done_tick = max((t.updated_tick for t in done), default=None)
    deadline = project.deadline_tick if project.deadline_tick is not None else WEEK_END_TICK

    people: dict[str, PersonReport] = {}
    for task in tasks:
        pid_person = task.assignee_id or "unassigned"
        report = people.get(pid_person)
        if report is None:
            person = store.get_person(pid_person)
            report = people[pid_person] = PersonReport(
                person_id=pid_person, name=person.name if person else pid_person)
        if task.status == "done":
            report.done.append(task)
            report.done_minutes += task.estimate_minutes
        else:
            report.remaining.append(task)
            report.remaining_minutes += task.estimate_minutes

    return EvalReport(
        project_id=pid,
        project_name=project.name,
        deadline_tick=deadline,
        total_tasks=len(tasks),
        done_tasks=len(done),
        total_minutes=sum(t.estimate_minutes for t in tasks),
        done_minutes=sum(t.estimate_minutes for t in done),
        last_done_tick=last_done_tick,
        goal_accomplished=(
            bool(tasks)
            and len(done) == len(tasks)
            and last_done_tick is not None
            and last_done_tick <= deadline
        ),
        people=sorted(people.values(), key=lambda p: p.person_id),
        remaining=remaining,
    )


def format_report(report: EvalReport) -> str:
    """Render the evaluation as human-readable text."""
    verdict = "ACCOMPLISHED" if report.goal_accomplished else "NOT ACCOMPLISHED"
    lines = [
        f"Project {report.project_id} — {report.project_name}",
        f"Goal: {verdict} — {report.done_tasks}/{report.total_tasks} tasks done, "
        f"{format_hours(report.done_minutes)} of {format_hours(report.total_minutes)} completed",
    ]
    if report.last_done_tick is not None:
        met = "met" if report.last_done_tick <= report.deadline_tick else "missed"
        lines.append(
            f"  last completion {format_tick(report.last_done_tick)} "
            f"(tick {report.last_done_tick}); deadline {format_tick(report.deadline_tick)} "
            f"(tick {report.deadline_tick}) — {met}"
        )
    lines.append("By person:")
    for p in report.people:
        lines.append(
            f"  {p.name:<8} {len(p.done)} done ({format_hours(p.done_minutes)}), "
            f"{len(p.remaining)} remaining ({format_hours(p.remaining_minutes)})"
        )
        for t in p.done:
            lines.append(
                f"      done {t.id:<8} {t.title:<48} at {format_tick(t.updated_tick)}"
            )
    lines.append("Remaining:")
    if not report.remaining:
        lines.append("  none")
    for t in report.remaining:
        lines.append(
            f"  {t.id:<8} {t.title:<48} {t.status:<12} {t.assignee_id or '-'}"
        )
    return "\n".join(lines)


def to_dict(report: EvalReport) -> dict[str, object]:
    """Serialize the report for JSON output (Issue models included as dicts)."""
    def issues(items: list[Issue]) -> list[dict[str, object]]:
        return [i.model_dump() for i in items]

    return {
        "project_id": report.project_id,
        "project_name": report.project_name,
        "deadline_tick": report.deadline_tick,
        "total_tasks": report.total_tasks,
        "done_tasks": report.done_tasks,
        "total_minutes": report.total_minutes,
        "done_minutes": report.done_minutes,
        "last_done_tick": report.last_done_tick,
        "goal_accomplished": report.goal_accomplished,
        "people": [
            {
                "person_id": p.person_id,
                "name": p.name,
                "done": issues(p.done),
                "done_minutes": p.done_minutes,
                "remaining": issues(p.remaining),
                "remaining_minutes": p.remaining_minutes,
            }
            for p in report.people
        ],
        "remaining": issues(report.remaining),
    }
