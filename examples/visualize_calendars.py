"""Weekly calendar per person from a run's ``world.db``, rendered to static HTML.

    uv run python examples/visualize_calendars.py <run_id>   # e.g. team_with_jira

Thin wrapper over :func:`pm.viz.write_calendars`: reads
``runs/<run_id>/world.db`` and writes ``runs/<run_id>/calendars.html`` — one
Mon–Fri 09:00–17:00 grid per person, blocks colored by kind (meeting / work /
OOO), positioned at 1px per tick (minute). Works for seed-state, mid-run, and
completed runs alike. Also available as ``uv run pm viz --scenario <scenario>``.
"""

from __future__ import annotations

import argparse

from pm.exceptions import ConfigurationError
from pm.viz import write_calendars


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", help="run id under runs/, e.g. team_with_jira")
    args = parser.parse_args(argv)
    try:
        out = write_calendars(args.run_id)
    except ConfigurationError as e:
        parser.error(e.message)
    print(out.resolve())


if __name__ == "__main__":
    main()
