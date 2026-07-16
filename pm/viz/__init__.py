"""Static-HTML visualizations over a run's ``world.db``.

Three read-only renderers: :mod:`pm.viz.calendars` (per-person Mon–Fri calendar
grids), :mod:`pm.viz.jira_tasks` (Gantt timeline of tickets over the 5-day ×
8-hour week + topological completion order), and :mod:`pm.viz.agent_activity`
(the LLM agent's logged actions on the week strip, from ``agent.jsonl``).
``write_*`` renders and writes into ``runs/<run_id>/``; ``render_*`` returns
the HTML for callers that bring their own data.
"""

from pm.viz.agent_activity import render_agent_activity_html, write_agent_activity
from pm.viz.calendars import render_calendars_html, write_calendars
from pm.viz.jira_tasks import render_jira_html, write_jira_tasks

__all__ = [
    "render_agent_activity_html",
    "render_calendars_html",
    "render_jira_html",
    "write_agent_activity",
    "write_calendars",
    "write_jira_tasks",
]
