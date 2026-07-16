"""The "Team with Jira" scenario — a GA-hardening week the team can *barely* finish.

The six-person cast (five implementers + the CTO) shares a standard meeting
fabric — daily standups, Alice's 1:1s, a Wednesday ad-hoc, and the Friday team
meeting — and the board is capacity-saturated: 66 pre-assigned tasks whose estimates tile each member's free
calendar segments between the pre-booked meetings exactly. Worked in dependency +
priority order (``priority`` = each member's chronological ordinal), the final
task — alice's GA sign-off — completes at exactly Fri 17:00 (tick 2400) with zero
idle minutes. Any deviation strands time behind a meeting (pending work yields
*past* an overlapping meeting, losing the pre-meeting gap) or idles on a blocked
dependency, and the week cannot absorb it: total work equals total capacity.

Capacity per member (2400-tick week minus meeting load): alice 2025, bob 2115,
clare 2115, david 2160, elieen 2160 — the per-member estimate sums below match
these exactly. Cross-member dependencies are just-in-time: each blocker's
intended completion tick <= its dependent's intended start tick.

``build(member_persona=...)`` seeds the five implementers with the given behavior
persona (default: :data:`pm.npc.persona.PERFECT`), so the same board can be re-seeded
with e.g. :data:`pm.npc.persona.FREE_SPIRIT` to show the team failing the week when
they pick tasks out of order.
"""

from __future__ import annotations

from collections.abc import Mapping

from pathlib import Path

from pm.env.environment import RUNS_DIR, Env
from pm.jira.api import JiraApi
from pm.jira.repository import JiraRepository
from pm.npc.cast import CAST as _FULL_CAST
from pm.npc.cast import MEMBERS as _CAST_MEMBERS
from pm.npc.cast import seed_cast, with_personas
from pm.npc.persona import PERFECT, Persona
from pm.sim.clock import MINUTES_PER_WORKDAY
from pm.sim.events import MeetingEvent
from pm.world.models import Project

SCENARIO = "team_with_jira"
PROJECT_ID = "GA"

# The scenario cast: the five implementers from pm.npc.cast plus the CTO.
_EXEC = next(c for c in _FULL_CAST if c.id == "xavier")
CAST = [*_CAST_MEMBERS, _EXEC]           # CastMember objects (5 members + the exec)
MEMBERS = [c.id for c in _CAST_MEMBERS]  # member ids (the exec doesn't implement)


def at(day: int, hour: int, minute: int = 0) -> int:
    """Tick for (weekday 0=Mon..4=Fri, hour, minute) on the work-hours calendar."""
    return day * MINUTES_PER_WORKDAY + (hour - 9) * 60 + minute

# Per-member tasks in chronological order: (title, estimate_minutes). Priority =
# the 1-based position in the list. The estimates tile the member's free segments
# between meetings (daily standup 11:00-11:30; alice's 1:1s Mon=bob/Tue=clare/
# Wed=david/Thu=elieen 14:00-14:30; Wed adhoc 13:00-13:45 alice+bob+clare; Fri
# team meeting 15:00-16:00) — e.g. a 120 fills a 09:00-11:00 morning exactly.
TASKS: dict[str, list[tuple[str, int]]] = {
    "alice": [
        ("Audit GA release checklist", 120),
        ("Fix auth token refresh in session API", 150),
        ("Add rate limiting to transcript API", 150),
        ("Repair flaky websocket reconnect", 120),
        ("Migrate session store schema", 150),
        ("Backfill transcripts for beta tenants", 150),
        ("Harden error paths in ingest service", 120),
        ("Tune ingest autoscaling thresholds", 90),
        ("Update on-call runbook", 15),
        ("Wire usage metering events", 150),
        ("Integrate STT fallback provider", 120),
        ("Load-test the full pipeline", 150),
        ("Fix defects from load test", 150),
        ("Stage rollout to canary tenants", 120),
        ("Verify canary metrics", 210),
        ("GA sign-off and release notes", 60),
    ],
    "bob": [
        ("Benchmark STT word-error rate", 120),
        ("Fix diarization drift on long calls", 150),
        ("Publish STT model v2 endpoint", 150),
        ("Profile STT latency under load", 120),
        ("Quantize model for CPU inference", 330),
        ("Add profanity filter pass", 120),
        ("Tune VAD thresholds", 90),
        ("Fix accent regression suite failures", 195),
        ("Add language auto-detect", 120),
        ("Retrain punctuation model on caption telemetry", 330),
        ("Evaluate v2 vs v1 A/B metrics", 120),
        ("Final model freeze and eval report", 210),
        ("Archive training artifacts", 60),
    ],
    "clare": [
        ("Fix caption flicker on resize", 120),
        ("Rewrite caption renderer for virtual scroll", 330),
        ("Integrate STT v2 streaming endpoint", 120),
        ("Add caption font and size settings", 150),
        ("Keyboard navigation for transcript pane", 150),
        ("Fix RTL rendering in captions", 120),
        ("Speaker color-coding in captions", 90),
        ("Ship caption telemetry events", 195),
        ("Fix caption offset on seek", 120),
        ("Offline transcript viewer", 330),
        ("Cross-browser caption QA fixes", 120),
        ("Polish pass from design QA", 210),
        ("Update captions changelog", 60),
    ],
    "david": [
        ("Add transcript table indexes", 120),
        ("Shard transcript storage by tenant", 330),
        ("Fix retention job memory leak", 120),
        ("Implement transcript search API", 330),
        ("Build search filters per UX spec", 120),
        ("Optimize search query plans", 150),
        ("Provision fallback STT service infra", 150),
        ("Add DB failover health checks", 120),
        ("Chaos-test storage failover", 330),
        ("Fix replication lag alerts", 120),
        ("Capacity plan and GA infra review", 210),
        ("Tag GA infra release", 60),
    ],
    "elieen": [
        ("Audit caption contrast ratios", 120),
        ("Redesign transcript reading view", 330),
        ("Spec caption settings panel", 120),
        ("Finalize transcript search UX spec", 330),
        ("Accessibility review: screen readers", 120),
        ("Design QA sweep of captions UI", 330),
        ("Fix icon set inconsistencies", 120),
        ("Mobile caption layout specs", 150),
        ("Empty-state and error illustrations", 150),
        ("Design QA of punctuated transcripts", 120),
        ("Final design sign-off review", 210),
        ("Archive design specs to docs", 60),
    ],
}

# Dependency edges: ((blocker_member, ordinal), (dependent_member, ordinal)).
# Cross-member edges are just-in-time (blocker's intended completion <= the
# dependent's intended start; several are exactly equal — same-tick handoff);
# same-member edges chain earlier work into later and hold trivially in order.
DEPS: list[tuple[tuple[str, int], tuple[str, int]]] = [
    # cross-member, just-in-time
    (("bob", 3), ("clare", 3)),      # v2 endpoint (Mon 17:00) -> streaming integration (Tue 09:00)
    (("david", 2), ("bob", 5)),      # storage shards (Mon 17:00) -> CPU quantization (Tue 11:30)
    (("clare", 2), ("elieen", 4)),   # renderer rewrite (Mon 17:00) -> search UX spec (Tue 11:30)
    (("elieen", 4), ("david", 5)),   # search UX spec (Tue 17:00) -> search filters (Wed 09:00)
    (("david", 7), ("alice", 11)),   # fallback infra (Wed 17:00) -> fallback integration (Thu 09:00)
    (("clare", 8), ("bob", 10)),     # caption telemetry (Wed 17:00) -> punctuation retrain (Thu 11:30)
    (("bob", 10), ("elieen", 10)),   # punctuation retrain (Thu 17:00) -> design QA of output (Fri 09:00)
    (("bob", 12), ("alice", 16)),    # Fri-afternoon work (ends 15:00) gates the
    (("clare", 12), ("alice", 16)),  # GA sign-off, which runs after the Fri team
    (("david", 11), ("alice", 16)),  # meeting and completes at exactly Fri 17:00
    (("elieen", 11), ("alice", 16)),
    # same-member story chains
    (("alice", 12), ("alice", 13)),  # load test -> fix its defects
    (("bob", 2), ("bob", 8)),        # diarization fix -> accent regression suite
    (("clare", 2), ("clare", 10)),   # renderer rewrite -> offline viewer
    (("david", 4), ("david", 6)),    # search API -> query-plan optimization
    (("elieen", 2), ("elieen", 6)),  # reading-view redesign -> design QA sweep
]

_STORIES: dict[str, str] = {
    "alice": "Integration & release",
    "bob": "STT quality & latency",
    "clare": "Captions UX",
    "david": "Pipeline & storage",
    "elieen": "Design QA & accessibility",
}


def _seed_board(env: Env) -> None:
    """1 epic, 5 per-member stories, 66 tasks tiling the week, then the dep edges."""
    env.store.add_project(Project(
        id=PROJECT_ID, name="Live Transcription GA", deadline_tick=at(4, 17)))
    repo = JiraRepository(env.store)
    repo.ensure_schema()
    api = JiraApi(repo, env.engine)

    epic = api.create_issue(PROJECT_ID, "epic", "GA hardening week", actor="xavier")
    disciplines = {c.id: c.discipline for c in CAST}
    keys: dict[tuple[str, int], str] = {}
    for member, rows in TASKS.items():
        story = api.create_issue(
            PROJECT_ID, "story", _STORIES[member], parent=epic.id, actor="alice")
        for ordinal, (title, minutes) in enumerate(rows, start=1):
            issue = api.create_issue(
                PROJECT_ID, "task", title, parent=story.id, estimate_minutes=minutes,
                assignee=member, component=disciplines[member], priority=ordinal,
                actor="alice")
            keys[(member, ordinal)] = issue.id

    # Link after all tasks exist: some blockers are created after their dependents.
    for blocker, dependent in DEPS:
        api.link_issue(keys[dependent], keys[blocker], actor="alice")


def _schedule_meetings(env: Env) -> None:
    """Eleven pre-defined meetings, queued as events (Alice owns/runs them)."""
    def meeting(mid, kind, day, hour, minute, dur, attendees, *, transcript=False, initiator=None):
        payload = {
            "meeting_id": mid, "kind": kind, "attendees": attendees,
            "title": {"standup": "Daily standup", "1on1": "1:1", "team": "Team meeting",
                      "adhoc": "Ad-hoc (external team)"}[kind],
        }
        if transcript:
            payload["transcript_id"] = f"tr-{mid}"
            payload["transcript_body"] = transcript if isinstance(transcript, str) \
                else f"{payload['title']} notes."
        env.engine.schedule(MeetingEvent(
            owner_id="alice", start_tick=at(day, hour, minute), duration=dur,
            payload=payload, initiator_id=initiator))

    # Daily standup 11:00-11:30, Mon-Fri, the whole team.
    for day in range(5):
        meeting(f"standup-{day}", "standup", day, 11, 0, 30, MEMBERS)

    # Alice's 1:1s with each other member, 30 min, Mon-Thu 14:00.
    for i, member in enumerate(["bob", "clare", "david", "elieen"]):
        meeting(f"1on1-{member}", "1on1", i, 14, 0, 30, ["alice", member])

    # Team meeting for all members, Fri 15:00-16:00.
    meeting("team-fri", "team", 4, 15, 0, 60, MEMBERS, transcript=True)

    # Ad-hoc from an external team, Wed 13:00-13:45.
    meeting("adhoc-ext", "adhoc", 2, 13, 0, 45, ["alice", "bob", "clare", "xavier"],
            initiator="external-team")


def build(run_id: str = SCENARIO, *, seed: int = 42, root: Path = RUNS_DIR,
          force: bool = True, member_persona: Persona | Mapping[str, Persona] = PERFECT) -> Env:
    """Create the run, seed cast + saturated board + meetings, snapshot ``seed.db``."""
    env = Env.make(SCENARIO, run_id, seed, force=force, root=root)
    seed_cast(env.store, cast=with_personas(CAST, member_persona))
    _seed_board(env)
    _schedule_meetings(env)
    env.store.db.backup_to(Env.seed_path(run_id, root))
    return env


if __name__ == "__main__":
    build()
    print(f"Built scenario {SCENARIO!r} at runs/{SCENARIO}/ (66 tasks tiling the "
          "week exactly; in priority+dependency order the board finishes at Fri 17:00 sharp).")
