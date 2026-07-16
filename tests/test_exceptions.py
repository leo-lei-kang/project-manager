"""Exception hierarchy: base contract and subsystem subclasses."""

from __future__ import annotations

import pytest

from pm.exceptions import (
    ConfigurationError,
    PMError,
    ScenarioError,
    ToolError,
    VerifierError,
    WorldStateError,
)


def test_base_carries_message_and_details() -> None:
    err = PMError("boom", details={"k": 1})
    assert err.message == "boom"
    assert err.details == {"k": 1}
    assert str(err) == "boom"


def test_details_defaults_to_empty_dict() -> None:
    assert PMError("x").details == {}


@pytest.mark.parametrize(
    "cls",
    [WorldStateError, ScenarioError, ToolError, VerifierError, ConfigurationError],
)
def test_subclasses_are_pm_errors(cls: type[PMError]) -> None:
    err = cls("msg")
    assert isinstance(err, PMError)
    assert err.message == "msg"
