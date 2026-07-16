"""Example: a realistic one-week sprint board built with the pm.jira API.

    uv run python examples/jira_board_example.py

Stands up the "Live Meeting Transcription" project — a single epic (the
deliverable) with five stories and twenty-five tasks, staffed by four engineers
and a designer, sized to roughly one focused work-week each. Tasks carry minute
estimates, assignees, and realistic cross-story `blocks` dependencies. Prints the
issue tree, the per-story + epic rollups, the derived blocked state, and how
finishing a kickoff task cascades to unblock its dependent.

`seed_world` and `build_board` are reused by `examples/jira_board_example_html.py`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import NamedTuple

from pm.db.store import Store
from pm.jira.api import JiraApi
from pm.jira.format import format_hours
from pm.jira.repository import JiraRepository
from pm.sim.engine import Engine
from pm.world.models import Person, Project

PROJECT_ID = "transcribe"
PROJECT_NAME = "Live Meeting Transcription"

# Each engineer/designer owns one story: alice->A, bob->B, clare->C, david->D, elieen->E.
TEAM = [
    ("alice", "Alice", "Backend"),
    ("bob", "Bob", "ML / Speech"),
    ("clare", "Clare", "Frontend"),
    ("david", "David", "Backend"),
    ("elieen", "Elieen", "Designer"),
]


class BoardRefs(NamedTuple):
    epic: str
    story_keys: list[str]
    kickoff: str           # a design task to finish in the demo cascade
    kickoff_unblocks: str  # the dependent that unblocks when `kickoff` is done


def seed_world(store: Store) -> None:
    """Add the project and the five team members the issues reference."""
    store.add_project(Project(id=PROJECT_ID, name=PROJECT_NAME))
    for pid, name, role in TEAM:
        store.add_person(Person(id=pid, name=name, role=role))


def build_board(api: JiraApi) -> BoardRefs:
    """Create the epic, five stories, twenty-five tasks, and their dependencies."""
    epic = api.create_issue(PROJECT_ID, "epic", "Live Meeting Transcription v1")

    # -- stories (one owner each) --------------------------------------------
    a = api.create_issue(PROJECT_ID, "story", "Audio capture pipeline", parent=epic.id)
    b = api.create_issue(PROJECT_ID, "story", "Speech-to-text integration", parent=epic.id)
    c = api.create_issue(PROJECT_ID, "story", "Real-time captions UI", parent=epic.id)
    d = api.create_issue(PROJECT_ID, "story", "Post-call transcript & storage", parent=epic.id)
    e = api.create_issue(PROJECT_ID, "story", "Transcription UX & design", parent=epic.id)

    def task(parent, title, minutes, who):
        return api.create_issue(
            PROJECT_ID, "task", title, parent=parent,
            estimate_minutes=minutes, assignee=who,
        )

    # -- Story A — Audio capture pipeline (alice) ----------------------------
    a1 = task(a.id, "Capture per-participant audio from WebRTC tracks", 360, "alice")
    a2 = task(a.id, "Chunk & buffer audio frames for streaming", 240, "alice")
    a3 = task(a.id, "Stream audio to STT gateway (websocket)", 360, "alice")
    a4 = task(a.id, "Reconnect, backpressure & error handling", 300, "alice")
    a5 = task(a.id, "Pipeline latency/load test harness", 240, "alice")

    # -- Story B — Speech-to-text integration (bob) --------------------------
    b1 = task(b.id, "Integrate streaming STT provider", 420, "bob")
    b2 = task(b.id, "Partial + final result handling", 300, "bob")
    b3 = task(b.id, "Speaker diarization / labeling", 360, "bob")
    b4 = task(b.id, "Language detection & config", 180, "bob")
    b5 = task(b.id, "Profanity / PII redaction pass", 300, "bob")

    # -- Story C — Real-time captions UI (clare) -----------------------------
    c1 = task(c.id, "Caption overlay component", 360, "clare")
    c2 = task(c.id, "Live caption rendering (partial->final)", 360, "clare")
    c3 = task(c.id, "Speaker name/color chips", 180, "clare")
    c4 = task(c.id, "Caption controls (toggle, size, language)", 180, "clare")

    # -- Story D — Post-call transcript & storage (david) --------------------
    d1 = task(d.id, "Transcript persistence schema & storage", 300, "david")
    d2 = task(d.id, "Transcript API (fetch + full-text search)", 360, "david")
    d3 = task(d.id, "Export transcript (TXT / WebVTT)", 180, "david")
    d4 = task(d.id, "Retention & access-control rules", 240, "david")
    d6 = task(d.id, "Realtime transcript sync to viewer", 300, "david")
    d5 = task(d.id, "Post-call transcript view page", 360, "david")

    # -- Story E — Transcription UX & design (elieen) ------------------------
    e1 = task(e.id, "Caption overlay design + motion spec", 300, "elieen")
    e2 = task(e.id, "Transcript view layout & search UX", 300, "elieen")
    e3 = task(e.id, "Caption states (partial/final/error/muted)", 240, "elieen")
    e4 = task(e.id, "Accessibility spec (caption a11y, contrast)", 240, "elieen")
    e5 = task(e.id, "Empty / loading / error states", 180, "elieen")

    # -- dependencies: link_issue(dependent, blocker) ------------------------
    links = [
        # within Audio pipeline
        (a2, a1), (a3, a2), (a4, a3), (a5, a3), (a5, a4),
        # STT builds on the audio stream
        (b1, a3), (b2, b1), (b3, b2), (b4, b1), (b5, b2), (b5, b4),
        # Captions UI follows the design specs and STT results
        (c1, e1), (c2, c1), (c2, b2), (c2, e3), (c3, b3), (c3, c1),
        (c4, c1), (c4, b4),
        # Transcript & storage build on STT output
        (d1, b2), (d1, b3), (d2, d1), (d2, b5), (d3, d1), (d3, b3),
        (d4, d1), (d6, d1), (d6, d2), (d5, d2), (d5, e2), (d5, d3),
        # Design internal ordering
        (e4, e3), (e4, e1), (e5, e2), (e5, e3),
    ]
    for dependent, blocker in links:
        api.link_issue(dependent.id, blocker.id)

    return BoardRefs(
        epic=epic.id,
        story_keys=[a.id, b.id, c.id, d.id, e.id],
        kickoff=e1.id,
        kickoff_unblocks=c1.id,
    )


def _print_tree(api: JiraApi) -> None:
    order = {"epic": 0, "story": 1, "task": 2}
    issues = sorted(
        api.search(project_id=PROJECT_ID),
        key=lambda i: (order[i.issue_type], i.id),
    )
    by_parent: dict[str | None, list] = {}
    for issue in issues:
        by_parent.setdefault(issue.parent_id, []).append(issue)

    def walk(parent: str | None, depth: int) -> None:
        for issue in by_parent.get(parent, []):
            indent = "  " * (depth + 1)
            who = f" @{issue.assignee_id}" if issue.assignee_id else ""
            dep = f"  ⟵ blocked by {issue.depends_on}" if issue.depends_on else ""
            est = f" est={format_hours(issue.estimate_minutes)}" if issue.estimate_minutes else ""
            print(
                f"{indent}{issue.id} [{issue.issue_type}] {issue.title} — "
                f"{issue.status}{who}{est}{dep}"
            )
            walk(issue.id, depth + 1)

    walk(None, 0)


def _print_rollups(api: JiraApi, refs: BoardRefs) -> None:
    for key in refs.story_keys:
        r = api.get_rollup(key)
        print(
            f"  {r.key} [{r.issue_type}] estimate={format_hours(r.estimate_minutes)} "
            f"remaining={format_hours(r.remaining_minutes)} leaves={r.leaf_count} status={r.by_status}"
        )
    epic = api.get_rollup(refs.epic)
    print(
        f"  {epic.key} [epic] DELIVERABLE TOTAL: estimate={format_hours(epic.estimate_minutes)} "
        f"remaining={format_hours(epic.remaining_minutes)} across {epic.leaf_count} tasks"
    )


def _blocked_ids(api: JiraApi) -> list[str]:
    return [i.id for i in api.search(project_id=PROJECT_ID, status="blocked")]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = Store.open(str(Path(tmp) / "world.db"), create=True)
        engine = Engine(store)
        repo = JiraRepository(store)
        repo.ensure_schema()
        api = JiraApi(repo, engine)

        seed_world(store)
        refs = build_board(api)

        print(f"=== {PROJECT_NAME} board ===")
        _print_tree(api)
        print("\n=== rollups ===")
        _print_rollups(api, refs)
        print(f"\nday-one actionable: {[i.id for i in api.search(project_id=PROJECT_ID, status='todo')]}")
        print(f"blocked: {_blocked_ids(api)}")

        # -- cascade: finish the caption design, watch the UI task unblock ---
        print(f"\n=== finish {refs.kickoff} (caption overlay design) ===")
        api.transition_issue(refs.kickoff, "in_progress")
        api.transition_issue(refs.kickoff, "done")
        print(f"  {refs.kickoff_unblocks} -> {api.get_issue(refs.kickoff_unblocks).status}")

        store.close()


if __name__ == "__main__":
    main()
