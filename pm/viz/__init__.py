"""Static-HTML visualizations over a run's ``world.db``.

Two read-only renderers: :mod:`pm.viz.calendars` (per-person Mon–Fri calendar
grids) and :mod:`pm.viz.jira_tasks` (Gantt timeline of tickets over the 5-day ×
8-hour week + topological completion order). ``write_*`` renders and writes
into ``runs/<run_id>/``; ``render_*`` returns the HTML for callers that bring
their own data.
"""

from pm.viz.calendars import render_calendars_html, write_calendars
from pm.viz.jira_tasks import render_jira_html, write_jira_tasks

__all__ = [
    "render_calendars_html",
    "render_jira_html",
    "write_calendars",
    "write_jira_tasks",
]
