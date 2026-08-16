"""Smoke tier — every public member actually answers.

The contract tier proves a member EXISTS and is documented; this proves it RUNS. A measure whose
SQL references a column the table does not have compiles fine and only fails when someone asks for
it — which, for an agent-facing layer, means it fails in front of a user.

Needs Cube up AND a warehouse behind it, so it skips cleanly on a Cube with no data.
"""
from __future__ import annotations

from collections.abc import Callable

import cube_client
import pytest
from contract.test_contract_core import EXPECTED_VIEWS

pytestmark = pytest.mark.smoke


@pytest.mark.parametrize("view", sorted(EXPECTED_VIEWS))
def test_every_measure_of_the_view_answers(meta: dict, load: Callable, view: str) -> None:
    """Query every measure of a view and report the ones that error."""
    errors = cube_client.measure_errors(meta, view, load)
    assert not errors, f"{view}: {errors}"


@pytest.mark.parametrize("view", sorted(EXPECTED_VIEWS))
def test_every_segment_of_the_view_filters(meta: dict, load: Callable, view: str) -> None:
    """Apply every segment and check it narrows rather than errors or multiplies.

    A segment that INCREASES a count is travelling through a join that fans out — the failure
    this catches is silent, because the query still returns a plausible number.
    """
    errors = cube_client.segment_errors(meta, view, load)
    assert not errors, f"{view}: {errors}"


@pytest.mark.parametrize("view", sorted(EXPECTED_VIEWS))
def test_every_time_dimension_slices(meta: dict, load: Callable, view: str) -> None:
    """Group by each time dimension at month granularity.

    Time slicing is the single most common thing asked of this layer, and a `sql:` expression that
    is not really a timestamp only reveals itself here.
    """
    time_dims = [d for d in cube_client.dimensions(meta, view) if d.get("type") == "time"]
    if not time_dims:
        pytest.skip(f"{view} declares no time dimension (catalog view)")
    measure = cube_client.member_names(cube_client.measures(meta, view))[0]
    for dim in time_dims:
        query = {
            "measures": [measure],
            "timeDimensions": [{"dimension": dim["name"], "granularity": "month"}],
            "limit": 3,
        }
        res = load(query)
        assert "data" in res, f"{view}: {dim['name']} failed to slice by month"
