"""Jira tickets on the work-week timeline, rendered to static HTML/SVG.

    uv run python examples/visualize_jira_tasks.py <run_id>   # e.g. tight_week

Thin wrapper over :func:`pm.viz.write_jira_tasks`: reads
``runs/<run_id>/world.db`` and writes ``runs/<run_id>/jira_tasks.html`` — a
Gantt-style inline-SVG timeline over the 5-day × 8-hour week (one lane per
person, one bar per ticket at its actually-worked interval, dependency arrows),
plus a numbered completion order from a deterministic topological sort. Also
available as ``uv run pm viz --scenario <scenario>``.
"""

from __future__ import annotations

import argparse

from pm.exceptions import ConfigurationError
from pm.viz import write_jira_tasks


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", help="run id under runs/, e.g. tight_week")
    args = parser.parse_args(argv)
    try:
        out = write_jira_tasks(args.run_id)
    except ConfigurationError as e:
        parser.error(e.message)
    print(out.resolve())


if __name__ == "__main__":
    main()
