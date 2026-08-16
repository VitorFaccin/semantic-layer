"""Contract tier — RBAC on both axes, plus the row filter.

Neither axis needs the warehouse: visibility is metadata (`/meta` compiled per security scope) and
the row filter is proven by reading the SQL Cube GENERATES, without executing it. That is what
makes this tier runnable in CI against a Cube with no warehouse credentials at all.

The personas mirror docs/access-control.md. Adding a level or a domain means adding a persona here.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

import cube_client
import pytest

pytestmark = pytest.mark.contract

# Views behind the level-40 floor: rep PII and billing/finance.
PII_AND_FINANCE_VIEWS = {"reps", "subscription", "renewals"}

PERSONAS = {
    "admin":              {"is_admin": True,  "level": 40, "domains": ["*"]},
    "exec_all":           {"is_admin": False, "level": 40, "domains": ["*"]},
    "director_elec":      {"is_admin": False, "level": 30, "domains": ["electronics"]},
    "analyst_appliances": {"is_admin": False, "level": 10, "domains": ["appliances"]},
    "analyst_furniture":  {"is_admin": False, "level": 10, "domains": ["furniture"]},
    "analyst_suppliers":  {"is_admin": False, "level": 10, "domains": ["suppliers"]},
}


def _views_for(persona: str) -> set:
    """Return the view names visible to a persona (access_policy applied at compile time).

    Args:
        persona (str): A key of PERSONAS.

    Returns:
        set: The names of the views that persona can see.
    """
    meta = cube_client.get_meta_as(PERSONAS[persona])
    return {v["name"] for v in cube_client.all_views(meta)}


def _generated_sql(persona: str, query: dict) -> tuple:
    """Return the SQL Cube WOULD run for a persona, without executing it.

    Reading the generated SQL is what lets the row filter be asserted with no warehouse behind
    Cube — the predicate and its bound parameters are visible before anything runs.

    Args:
        persona (str): A key of PERSONAS.
        query (dict): The Cube query to compile.

    Returns:
        tuple: (sql_text, bound_params).
    """
    token = cube_client.mint_jwt(PERSONAS[persona])
    url = f"{cube_client._API}/sql?" + urllib.parse.urlencode({"query": json.dumps(query)})
    req = urllib.request.Request(url, headers={"Authorization": token})
    with urllib.request.urlopen(req, timeout=90) as resp:
        block = json.load(resp)["sql"]["sql"]
    return block[0], block[1]


# --------------------------------------------------------------------- axis 1: visibility

def test_admin_sees_every_view() -> None:
    """The override axis — admin is a superset of every other persona."""
    admin = _views_for("admin")
    for persona in PERSONAS:
        assert _views_for(persona) <= admin, f"{persona} sees a view admin does not"


@pytest.mark.parametrize("persona", ["analyst_appliances", "analyst_furniture",
                                     "analyst_suppliers", "director_elec"])
def test_level_floor_hides_pii_and_finance(persona: str) -> None:
    """Check the LEVEL axis: below 40 the PII/finance views are invisible.

    That holds even for a director, so breadth of seniority alone never unlocks personal data.
    """
    assert not (_views_for(persona) & PII_AND_FINANCE_VIEWS), (
        f"{persona} (level < 40) can see {_views_for(persona) & PII_AND_FINANCE_VIEWS}"
    )


def test_level_40_without_admin_unlocks_pii_and_finance() -> None:
    """Same axis, other direction: level 40 is sufficient — admin is not required."""
    assert PII_AND_FINANCE_VIEWS <= _views_for("exec_all")


@pytest.mark.parametrize("persona,mine,not_mine", [
    ("analyst_appliances", "operational_appliances", "operational_furniture"),
    ("analyst_furniture", "operational_furniture", "operational_electronics"),
    ("director_elec", "operational_electronics", "operational_appliances"),
])
def test_domain_axis_isolates_the_vertical_views(persona: str, mine: str, not_mine: str) -> None:
    """The DOMAIN axis: a vertical's views are visible only to that vertical's people."""
    views = _views_for(persona)
    assert mine in views, f"{persona} cannot see its own vertical view {mine}"
    assert not_mine not in views, f"{persona} leaked into another vertical: {not_mine}"


def test_supplier_analyst_sees_supply_side_but_no_vertical_pipeline() -> None:
    """The supply-side domain is orthogonal to the product verticals."""
    views = _views_for("analyst_suppliers")
    assert "supplier_performance" in views
    assert not [v for v in views if v.startswith("operational_")]


# --------------------------------------------------------------------- axis 2: row filter

_REVENUE_QUERY = {"measures": ["sales.total_sale_amount"], "dimensions": ["sales.product_line"]}


def test_admin_query_carries_no_row_filter() -> None:
    """Wildcard/admin scope is deliberately unfiltered — otherwise rollups would be per-scope."""
    _, params = _generated_sql("admin", _REVENUE_QUERY)
    assert not params, f"admin query was filtered with {params}"


@pytest.mark.parametrize("persona,expected_line", [
    ("analyst_appliances", "Appliances"),
    ("analyst_furniture", "Furniture"),
])
def test_domain_analyst_query_is_confined_to_its_product_line(
    persona: str, expected_line: str,
) -> None:
    """query_rewrite injects `product_line IN (...)` on the transversal views."""
    sql, params = _generated_sql(persona, _REVENUE_QUERY)
    assert "product_line" in sql, f"{persona}: no product_line predicate in the generated SQL"
    assert expected_line in params, f"{persona}: expected {expected_line!r} in params, got {params}"


def test_domain_with_no_product_line_fails_closed() -> None:
    """Check the suppliers asymmetry: no product line means no rows.

    A domain that maps to NO product line must get a deny-all predicate, never an unfiltered
    query.

    Failure closes.
    """
    _, params = _generated_sql("analyst_suppliers", _REVENUE_QUERY)
    assert "__no_access__" in params, (
        f"suppliers analyst was not denied on a revenue view — params were {params}"
    )


def test_segment_cannot_bypass_the_row_filter() -> None:
    """A segment is a WHERE clause the CALLER chooses; the row filter must still AND onto it.

    This is the bypass an attacker would try first.
    """
    meta = cube_client.get_meta_as(PERSONAS["analyst_appliances"])
    segs = cube_client.member_names(cube_client.segments(meta, "sales"))
    if not segs:
        pytest.skip("the sales view declares no segment to test against")
    _, params = _generated_sql("analyst_appliances",
                               {"measures": ["sales.total_sale_amount"], "segments": [segs[0]]})
    assert "Appliances" in params, (
        f"segment {segs[0]} dropped the row filter — params were {params}"
    )
