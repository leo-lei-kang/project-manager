"""Exception hierarchy for the Project Manager Simulation.

Mirrors fleet-sdk's ``fleet/exceptions.py`` shape: a single base carrying a
``message`` plus a free-form ``details`` dict, with focused subclasses per
subsystem so callers can catch at the granularity they need.
"""

from __future__ import annotations

from typing import Any


class PMError(Exception):
    """Base exception for all Project Manager Simulation errors."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class WorldStateError(PMError):
    """Raised when a world-state mutation or lookup is invalid.

    Examples: mutating an unknown entity, or violating an invariant the schema
    cannot express on its own.
    """


class ScenarioError(PMError):
    """Raised when a scenario fails to load or validate."""


class ToolError(PMError):
    """Raised when an agent-facing tool cannot complete its action."""


class VerifierError(PMError):
    """Raised for a genuine grader fault (bug/misconfiguration).

    Distinct from a *failing* verification: a failed check yields score 0.0,
    whereas this signals the verifier itself could not run correctly.
    """


class ConfigurationError(PMError):
    """Raised when configuration is missing or invalid.

    Examples: an incompatible database schema version, or an LLM-backed feature
    invoked without its optional dependency installed.
    """
