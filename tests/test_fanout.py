"""Fan-out tier — a join must never multiply the fact it decorates.

This is the most expensive class of bug in dimensional modelling, because it does not raise: the
query succeeds and returns a number that is simply too big. Revenue quietly doubles the moment
someone groups by an attribute that lives on the wrong side of a one-to-many join.

The invariant: adding a joined DIMENSION to a query may split a total across more rows, but the
total itself must not change. If it grows, the join is duplicating fact rows.

Needs Cube up AND a warehouse behind it.
"""
from __future__ import annotations

from collections.abc import Callable

import pytest

pytestmark = pytest.mark.fanout

# (view, additive measure, dimension reached THROUGH a join). Only additive measures belong here:
# a count-distinct or a ratio legitimately changes when the grain changes, so it proves nothing.
FACT_VIEWS_WITH_JOINED_DIMENSIONS = [
    ("sales", "sales.total_sale_amount", "sales.base_reps_region"),
    ("sales", "sales.total_commission_amount", "sales.base_reps_tier"),
    ("orders", "orders.total_assumed_value", "orders.base_reps_region"),
    ("quotes", "quotes.total_quoted_value", "quotes.base_reps_territory"),
    ("operational_appliances", "operational_appliances.total_deal_amount",
     "operational_appliances.base_reps_region"),
    ("operational_furniture", "operational_furniture.total_deal_amount",
     "operational_furniture.base_reps_tier"),
    ("operational_electronics", "operational_electronics.total_deal_amount",
     "operational_electronics.base_reps_region"),
]


def _total(load: Callable, measure: str, dimension: str | None = None) -> float:
    """Sum a measure, optionally after grouping by a dimension.

    Args:
        load (Callable): The query runner.
        measure (str): Qualified measure name.
        dimension (str | None): Qualified dimension to group by, or None for the grand total.

    Returns:
        float: The summed value across every returned row.
    """
    query: dict = {"measures": [measure]}
    if dimension:
        query["dimensions"] = [dimension]
    rows = load(query).get("data", [])
    return sum(float(r[measure] or 0) for r in rows)


@pytest.mark.parametrize("view,measure,dimension", FACT_VIEWS_WITH_JOINED_DIMENSIONS,
                         ids=[f"{v}:{d.split('.')[-1]}" for v, _, d in
                              FACT_VIEWS_WITH_JOINED_DIMENSIONS])
def test_joined_dimension_does_not_inflate_an_additive_total(
    load: Callable, view: str, measure: str, dimension: str,
) -> None:
    """The grand total must survive being broken down by a joined attribute."""
    grand = _total(load, measure)
    broken_down = _total(load, measure, dimension)
    assert grand > 0, f"{measure} returned zero — the fixture data cannot prove anything"
    # Cent-level tolerance: the two paths sum floats in a different order.
    assert abs(grand - broken_down) < 0.01, (
        f"FAN-OUT in {view}: {measure} is {grand:,.2f} on its own but {broken_down:,.2f} when "
        f"grouped by {dimension} — the join is multiplying fact rows"
    )


def test_rep_attributes_come_from_a_unique_key(meta: dict, load: Callable) -> None:
    """The shared rep attribute block joins many_to_one on a unique key.

    Every fact view offers the same rep cuts through that macro, so one duplicated rep in the
    source would inflate every one of them at once. Assert the join key is unique where it lives.
    """
    rows = load({"measures": ["reps.reps_count"]}).get("data", [])
    total_reps = float(rows[0]["reps.reps_count"])
    by_region = _total(load, "reps.reps_count", "reps.region")
    assert abs(total_reps - by_region) < 0.01, (
        f"reps_count is {total_reps} overall but {by_region} when split by region — "
        "the rep dimension is not unique on its key"
    )
