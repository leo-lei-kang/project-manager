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
class EpicProgress:
    """One epic's completion: leaf tasks done / total, their estimate minutes,
    and the individual tickets (numeric key order)."""

    id: str
    title: str
    priority: int
    done_tasks: int
    total_tasks: int
    done_minutes: int
    total_minutes: int
    tasks: list[TaskView] = field(default_factory=list)


@dataclass
class TaskReconciliation:
    """One notes task matched (or not) against the Jira board.

    Pairing is by normalized title, preferring the same assignee; each board
    ticket pairs with at most one notes task. ``status_match`` means the task
    was filed AND the statuses agree (Jira ``in_review`` counts as the notes'
    ``in_progress``).
    """

    notes_id: str
    title: str
    dri_id: str
    notes_status: str
    jira_key: str | None  # None = never filed on the board
    jira_status: str | None
    status_match: bool


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
    # Per-epic completion over the Jira board (empty when the run has no epics).
    epics: list[EpicProgress] = field(default_factory=list)
    # Notes <-> board pairing, one row per informal task (empty on board-only runs).
    reconciliation: list[TaskReconciliation] = field(default_factory=list)
    notes_filed: int = 0
    notes_status_matched: int = 0
    # Hours of informal work, from the notes' own estimates (0 on board-only runs).
    done_notes_minutes: int = 0
    total_notes_minutes: int = 0
    # LLM agent usage, summed from the run's agent.jsonl (zero when absent).
    agent_llm_calls: int = 0
    agent_input_tokens: int = 0
    agent_output_tokens: int = 0


def _norm(title: str) -> str:
    """Titles compared casefolded with whitespace collapsed."""
    return " ".join(title.split()).casefold()


def _task_order(task_id: str) -> tuple[str, int]:
    """Sort key putting NOTES-9 before NOTES-10 (numeric suffix, not lexicographic)."""
    prefix, _, suffix = task_id.rpartition("-")
    return (prefix, int(suffix)) if suffix.isdigit() else (task_id, 0)


def _reconcile(informal, issues) -> list[TaskReconciliation]:
    """Pair each notes task with a board ticket by title, one to one."""
    by_title: dict[str, list] = {}
    for i in issues:
        by_title.setdefault(_norm(i.title), []).append(i)
    used: set[str] = set()
    rows = []
    for t in informal:
        candidates = [i for i in by_title.get(_norm(t.title), []) if i.id not in used]
        match = next((i for i in candidates if i.assignee_id == t.dri_id),
                     candidates[0] if candidates else None)
        if match is not None:
            used.add(match.id)
        jira_status = match.status if match is not None else None
        # Notes have no in_review state — a board ticket parked there is
        # still "being worked" from the notes' point of view.
        comparable = "in_progress" if jira_status == "in_review" else jira_status
        rows.append(TaskReconciliation(
            notes_id=t.id, title=t.title, dri_id=t.dri_id or "unassigned",
            notes_status=t.status, jira_key=match.id if match else None,
            jira_status=jira_status,
            status_match=match is not None and comparable == t.status,
        ))
    return rows


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
    repo = JiraRepository(store)
    issues = repo.list_issues(project_id=pid, issue_type="task")
    # Run-global (a run holds one project), in NOTES-<number> order so every
    # report section (reconciliation, remaining, per-person) reads naturally.
    informal = sorted(store.list_tasks(), key=lambda t: _task_order(t.id))

    def _epic_row(id: str, title: str, priority: int, subtasks: list) -> EpicProgress:
        subtasks = sorted(subtasks, key=lambda i: _task_order(i.id))
        done_sub = [t for t in subtasks if t.status == "done"]
        return EpicProgress(
            id=id, title=title, priority=priority,
            done_tasks=len(done_sub), total_tasks=len(subtasks),
            done_minutes=sum(t.estimate_minutes for t in done_sub),
            total_minutes=sum(t.estimate_minutes for t in subtasks),
            tasks=[TaskView(t.id, t.title, t.assignee_id or "unassigned", t.status)
                   for t in subtasks],
        )

    epics = []
    covered: set[str] = set()
    for epic in repo.list_issues(project_id=pid, issue_type="epic"):
        subtasks = [i for i in repo.subtree(epic.id) if i.issue_type == "task"]
        covered.update(t.id for t in subtasks)
        epics.append(_epic_row(epic.id, epic.title, epic.priority, subtasks))
    # Tickets outside every epic (e.g. agent-filed project work) get the same
    # progress line, as a synthetic row identified by an empty id.
    orphans = [i for i in issues if i.id not in covered]
    if orphans:
        epics.append(_epic_row("", "tasks without an epic", 0, orphans))

    if informal:
        source = "notes"
        views = [TaskView(t.id, t.title, t.dri_id or "unassigned", t.status)
                 for t in informal]
    else:
        source = "board"
        views = [TaskView(i.id, i.title, i.assignee_id or "unassigned", i.status)
                 for i in issues]

    reconciliation = _reconcile(informal, issues)

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
        epics=epics,
        reconciliation=reconciliation,
        notes_filed=sum(1 for r in reconciliation if r.jira_key is not None),
        notes_status_matched=sum(1 for r in reconciliation if r.status_match),
        done_notes_minutes=sum(t.estimate_minutes for t in informal
                               if t.status == "done"),
        total_notes_minutes=sum(t.estimate_minutes for t in informal),
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
    if report.total_notes_minutes:
        lines.insert(2, f"Notes tasks done: {format_hours(report.done_notes_minutes)} "
                        f"of {format_hours(report.total_notes_minutes)}")
    for e in report.epics:
        label = f"Epic {e.id} (p{e.priority}) {e.title}" if e.id else "Tasks without an epic"
        lines.append(
            f"{label}: {e.done_tasks}/{e.total_tasks} "
            f"tasks done, {format_hours(e.done_minutes)} of {format_hours(e.total_minutes)}"
        )
        for t in e.tasks:
            lines.append(f"  {t.id:<8} {t.title:<48} {t.status:<12} {t.owner_id}")
    if report.reconciliation:
        n = len(report.reconciliation)
        lines.append(f"Notes <-> board: filed {report.notes_filed}/{n}, "
                     f"statuses agree {report.notes_status_matched}/{n}")
        for r in report.reconciliation:
            if r.jira_key is None:
                what = "-> not filed"
            elif r.status_match:
                what = f"-> {r.jira_key:<7} {r.notes_status}"
            else:
                what = (f"-> {r.jira_key:<7} notes={r.notes_status} "
                        f"board={r.jira_status}  MISMATCH")
            lines.append(f"  {r.notes_id:<9} {r.title:<48} {what}")
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
        "epics": [asdict(e) for e in report.epics],
        "reconciliation": [asdict(r) for r in report.reconciliation],
        "notes_filed": report.notes_filed,
        "notes_status_matched": report.notes_status_matched,
        "done_notes_minutes": report.done_notes_minutes,
        "total_notes_minutes": report.total_notes_minutes,
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
