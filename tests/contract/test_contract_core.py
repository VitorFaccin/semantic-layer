"""Contract tier — the model compiles and the public storefront is what we think it is.

Meta-only: needs a running Cube, never the warehouse. This is the tier that fails first and
loudest when a YAML edit breaks compilation (a duplicated key, a bad Jinja interpolation), so it
is the cheapest guard in the suite.
"""
from __future__ import annotations

import cube_client
import pytest

pytestmark = pytest.mark.contract

# The public storefront. Adding a view is a deliberate act — it must show up here too, otherwise
# a view can be published to the agent without anyone reviewing its descriptions or its policy.
EXPECTED_VIEWS = {
    # transversal (commercial core)
    "sales", "orders", "quotes", "commissions", "funnel", "products", "customers",
    # people / activity
    "reps", "rep_activity", "logins",
    # per-product-line pipelines (one per vertical, symmetric by `extends`)
    "operational_appliances", "operational_furniture", "operational_electronics",
    "ranking_rep_appliances", "ranking_rep_furniture", "ranking_rep_electronics",
    # supply side
    "supplier_performance",
    # acquisition
    "leads", "lead_funnel", "growth",
    # recurring revenue
    "subscription", "renewals",
}


def test_model_compiles(meta: dict) -> None:
    """/meta answering at all means every YAML parsed and every Jinja macro rendered."""
    assert meta.get("cubes"), "/meta returned no cubes — the model failed to compile"


def test_public_storefront_is_exactly_the_expected_views(meta: dict) -> None:
    """The set of PUBLIC views is closed and reviewed — no accidental publishing."""
    actual = {v["name"] for v in cube_client.all_views(meta)}
    assert actual == EXPECTED_VIEWS, (
        f"storefront drift — only in Cube: {sorted(actual - EXPECTED_VIEWS)}; "
        f"only in the expected list: {sorted(EXPECTED_VIEWS - actual)}"
    )


def test_no_private_cube_is_exposed(meta: dict) -> None:
    """The governance boundary: `base_*` cubes are private, the agent must never see one.

    A column outside a view does not exist for the AI — that only holds if no cube leaks.
    """
    leaked = [c["name"] for c in cube_client.all_cubes(meta) if c["name"].startswith("base_")]
    assert not leaked, f"private cubes exposed through /meta: {leaked}"


# Catalog views describe entities that simply exist, with no event date to slice by. They answer
# "does this product exist / what is it?", never "how many last month?" — so the time-dimension
# requirement below does not apply to them.
CATALOG_VIEWS = {"products"}


@pytest.mark.parametrize("view", sorted(EXPECTED_VIEWS))
def test_view_has_measures(meta: dict, view: str) -> None:
    """A view with no measures is not queryable in any useful way."""
    assert cube_client.measures(meta, view), f"{view}: no measures"


@pytest.mark.parametrize("view", sorted(EXPECTED_VIEWS - CATALOG_VIEWS))
def test_fact_view_is_sliceable_over_time(meta: dict, view: str) -> None:
    """Check that every FACT view is sliceable over time.

    A fact view with no time dimension silently kills every "no mês passado" question the agent
    will be asked.
    """
    dims = cube_client.dimensions(meta, view)
    assert any(d.get("type") == "time" for d in dims), f"{view}: no time dimension"


@pytest.mark.parametrize("view", sorted(EXPECTED_VIEWS))
def test_members_are_snake_case(meta: dict, view: str) -> None:
    """Naming convention — mixed casing makes the agent guess member names."""
    bad = [
        n for n in cube_client.member_names(
            cube_client.measures(meta, view)
            + cube_client.dimensions(meta, view)
            + cube_client.segments(meta, view)
        )
        if n != n.lower()
    ]
    assert not bad, f"{view}: non snake_case members {bad}"
