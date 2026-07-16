"""Evaluate a run's project outcome from its ``world.db`` — read-only.

The evaluator grades one question: is the run's high-priority project done?
The project's task list comes from the informal ``task`` table — the meeting
notes' mirror of the full project breakdown, including work that never reached
the Jira board. When a run has no informal tasks (the Epic-board scenarios),
the board's leaf ``task`` issues stand in. The **goal is accomplished** when
every project task is ``done``; the board being empty or holding open tickets
is not itself a verdict, and there is no deadline check.

Hours stay Jira-sourced: informal tasks carry no estimates, so the report
separately sums the estimates of closed (``done``) Jira leaf tasks. A done
task's remaining work is logged out at completion, so its estimate equals the
effort spent. Only leaf ``task`` issues are counted (stories/epics are derived
rollups over them, so counting all types would double-count).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from pm.agent.log import read_agent_logs
from pm.db.store import Store
from pm.exceptions import ConfigurationError
from pm.jira.format import format_hours
from pm.jira.repository import JiraRepository


@dataclass
class TaskView:
    """A project task normalized across its two sources (notes row / Jira issue)."""

    id: str
    title: str
    owner_id: str
    status: str


@dataclass
class PersonReport:
    """One person's slice of the project: what they finished and what they still hold."""

    person_id: str
    name: str
    done: list[TaskView] = field(default_factory=list)
    remaining: list[TaskView] = field(default_factory=list)


@dataclass
class EvalReport:
    """The evaluation of one project at the moment the store was read."""

    project_id: str
    project_name: str
    source: str  # "notes" (informal task table) or "board" (Jira fallback)
    total_tasks: int
    done_tasks: int
    goal_accomplished: bool
    closed_jira_minutes: int
    total_jira_minutes: int
    people: list[PersonReport]
    remaining: list[TaskView]
    # LLM agent usage, summed from the run's agent.jsonl (zero when absent).
    agent_llm_calls: int = 0
    agent_input_tokens: int = 0
    agent_output_tokens: int = 0


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


def evaluate(
    store: Store, project_id: str | None = None, *, run_dir: Path | None = None
) -> EvalReport:
    """Evaluate ``project_id`` (or the run's only project) from ``store``.

    ``run_dir`` (the ``runs/<scenario>/`` folder) additionally sums the LLM
    agent's token usage from its ``agent-<model>.jsonl`` logs, when any exist.
    """
    pid = _resolve_project_id(store, project_id)
    project = store.get_project(pid)
    assert project is not None  # _resolve_project_id verified existence
    issues = JiraRepository(store).list_issues(project_id=pid, issue_type="task")
    informal = store.list_tasks()  # run-global; a run holds one project

    if informal:
        source = "notes"
        views = [TaskView(t.id, t.title, t.dri_id or "unassigned", t.status)
                 for t in informal]
    else:
        source = "board"
        views = [TaskView(i.id, i.title, i.assignee_id or "unassigned", i.status)
                 for i in issues]

    done = [v for v in views if v.status == "done"]
    remaining = [v for v in views if v.status != "done"]

    people: dict[str, PersonReport] = {}
    for view in views:
        report = people.get(view.owner_id)
        if report is None:
            person = store.get_person(view.owner_id)
            report = people[view.owner_id] = PersonReport(
                person_id=view.owner_id, name=person.name if person else view.owner_id)
        (report.done if view.status == "done" else report.remaining).append(view)

    llm_calls = []
    if run_dir is not None:
        llm_calls = [e for e in read_agent_logs(run_dir)
                     if e.get("kind") == "llm_call"]

    return EvalReport(
        project_id=pid,
        project_name=project.name,
        source=source,
        total_tasks=len(views),
        done_tasks=len(done),
        goal_accomplished=bool(views) and not remaining,
        closed_jira_minutes=sum(i.estimate_minutes for i in issues if i.status == "done"),
        total_jira_minutes=sum(i.estimate_minutes for i in issues),
        people=sorted(people.values(), key=lambda p: p.person_id),
        remaining=remaining,
        agent_llm_calls=len(llm_calls),
        agent_input_tokens=sum(e.get("input_tokens", 0) for e in llm_calls),
        agent_output_tokens=sum(e.get("output_tokens", 0) for e in llm_calls),
    )


def format_report(report: EvalReport) -> str:
    """Render the evaluation as human-readable text."""
    verdict = "PROJECT DONE" if report.goal_accomplished else "PROJECT NOT DONE"
    lines = [
        f"Project {report.project_id} — {report.project_name}",
        f"Goal: {verdict} — {report.done_tasks}/{report.total_tasks} tasks done "
        f"(source: {report.source})",
        f"Jira tickets closed: {format_hours(report.closed_jira_minutes)} of "
        f"{format_hours(report.total_jira_minutes)}",
    ]
    if report.agent_llm_calls:
        lines.append(
            f"Agent LLM usage: {report.agent_llm_calls} calls, "
            f"{report.agent_input_tokens} in / {report.agent_output_tokens} out tokens"
        )
    lines.append("By person:")
    for p in report.people:
        lines.append(f"  {p.name:<8} {len(p.done)} done, {len(p.remaining)} remaining")
        for t in p.done:
            lines.append(f"      done {t.id:<8} {t.title}")
    lines.append("Remaining:")
    if not report.remaining:
        lines.append("  none")
    for t in report.remaining:
        lines.append(f"  {t.id:<8} {t.title:<48} {t.status:<12} {t.owner_id}")
    return "\n".join(lines)


def to_dict(report: EvalReport) -> dict[str, object]:
    """Serialize the report for JSON output (TaskViews included as dicts)."""
    def tasks(items: list[TaskView]) -> list[dict[str, object]]:
        return [asdict(t) for t in items]

    return {
        "project_id": report.project_id,
        "project_name": report.project_name,
        "source": report.source,
        "total_tasks": report.total_tasks,
        "done_tasks": report.done_tasks,
        "goal_accomplished": report.goal_accomplished,
        "closed_jira_minutes": report.closed_jira_minutes,
        "total_jira_minutes": report.total_jira_minutes,
        "agent": {
            "llm_calls": report.agent_llm_calls,
            "input_tokens": report.agent_input_tokens,
            "output_tokens": report.agent_output_tokens,
        },
        "people": [
            {
                "person_id": p.person_id,
                "name": p.name,
                "done": tasks(p.done),
                "remaining": tasks(p.remaining),
            }
            for p in report.people
        ],
        "remaining": tasks(report.remaining),
    }
