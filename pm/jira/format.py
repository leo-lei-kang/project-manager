"""Display helpers for Jira issues.

Effort is *stored* in minutes (``estimate_minutes`` / ``remaining_minutes``);
this renders it as float hours for humans, showing fractions only when needed.
"""

from __future__ import annotations


def format_hours(minutes: int) -> str:
    """Render a minute duration as float hours.

    ``30 -> '0.5h'``, ``90 -> '1.5h'``, ``360 -> '6h'``, ``0 -> '0h'``.
    """
    return f"{minutes / 60:g}h"
