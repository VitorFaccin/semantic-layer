"""Contract tier — the Cube features this repository exists to demonstrate.

Every check here pins a MODELLING FEATURE, not a business number: nested folders, hierarchies,
custom granularities, multi-stage measures (time_shift / group_by), rolling windows,
pre-aggregations and `extends` symmetry. If a Cube upgrade drops or renames one of them, this
file is what tells you.

Read alongside README.md › "Cube features, and where each one lives".
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

import cube_client
import pytest

pytestmark = pytest.mark.contract


def _extended_meta() -> dict:
    """Fetch /meta?extended — the only response that carries the nested folder tree.

    Returns:
        dict: The extended metadata payload.
    """
    token = cube_client.mint_jwt({"is_admin": True, "level": 40, "domains": ["*"]})
    req = urllib.request.Request(f"{cube_client._API}/meta?extended",
                                 headers={"Authorization": token})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.load(resp)


# ------------------------------------------------------------------ folders & hierarchies

def test_nested_folders_are_exposed_as_a_tree() -> None:
    """Plain `folders` flattens by design; `nestedFolders` keeps the tree.

    The sales view nests a temporal sub-folder inside the commission folder — that nesting must
    survive.
    """
    meta = _extended_meta()
    sales = next(c for c in meta["cubes"] if c["name"] == "sales")
    nested = sales.get("nestedFolders") or []
    assert nested, "sales view exposes no nestedFolders"
    has_child = any(
        any(isinstance(m, dict) and m.get("members") is not None for m in (f.get("members") or []))
        for f in nested
    )
    assert has_child, "no folder inside a folder — the nesting was flattened"


def test_hierarchies_are_declared(meta: dict) -> None:
    """Hierarchies let the agent drill down a known path instead of guessing dimension order."""
    with_h = [v["name"] for v in cube_client.all_views(meta) if v.get("hierarchies")]
    assert with_h, "no view declares a hierarchy"


# ------------------------------------------------------------------ time modelling

def test_custom_granularity_is_available_on_the_sales_time_dimension(meta: dict) -> None:
    """A business calendar week (starting Sunday) is a custom granularity, not a built-in one."""
    dims = cube_client.dimensions(meta, "sales")
    time_dims = [d for d in dims if d.get("type") == "time"]
    customs = {g.get("name") for d in time_dims for g in (d.get("granularities") or [])}
    builtin = {"second", "minute", "hour", "day", "week", "month", "quarter", "year"}
    assert customs - builtin, f"no custom granularity found (only {sorted(customs)})"


def test_multi_stage_and_rolling_window_measures_exist(meta: dict) -> None:
    """Check that the multi-stage and rolling-window measures survive as real measures.

    Multi-stage (time_shift / group_by) and rolling windows are the Tesseract-era features that
    replace client-side post-processing.

    They must survive as real measures.
    """
    names = set(cube_client.short_names(cube_client.measures(meta, "sales")))
    expected = {"revenue_prior_period", "revenue_of_line", "revenue_share_of_line",
                "revenue_trailing_12m"}
    missing = expected - names
    assert not missing, f"sales view lost multi-stage/rolling measures: {sorted(missing)}"


# ------------------------------------------------------------------ caching

def test_declared_pre_aggregations_are_used_by_a_matching_query() -> None:
    """Check that a query matching a rollup is served from the cache.

    This is the point of the semantic layer's cache: a matching query must be rewritten to read
    the Cube Store partition instead of the warehouse.
    """
    token = cube_client.mint_jwt({"is_admin": True, "level": 40, "domains": ["*"]})
    query = {
        "measures": ["sales.total_sale_amount"],
        "dimensions": ["sales.product_line"],
        "timeDimensions": [{"dimension": "sales.sold_at", "granularity": "month"}],
    }
    url = f"{cube_client._API}/sql?" + urllib.parse.urlencode({"query": json.dumps(query)})
    req = urllib.request.Request(url, headers={"Authorization": token})
    with urllib.request.urlopen(req, timeout=90) as resp:
        sql = json.load(resp)["sql"]["sql"][0]
    assert "revenue_by_line_monthly" in sql, (
        "the monthly revenue query did NOT hit its pre-aggregation — it would go to the "
        f"warehouse. Generated SQL: {sql[:300]}"
    )


# ------------------------------------------------------------------ reuse: extends

VERTICALS = ["appliances", "furniture", "electronics"]


# The architecture is "shared vertical skeleton + per-line specifics": every pipeline view
# inherits the same core from the abstract cube (`extends`) and the same rep attribute block
# (macro), then adds the handful of members that only make sense for that product line.
# Both halves are invariants — the core must not drift, and the specifics must stay declared.
VERTICAL_SPECIFIC_MEMBERS = {
    "appliances": {"installation_required", "warranty_months"},
    "furniture": {"is_custom_order", "lead_time_days"},
    "electronics": {"is_bundle", "trade_in_value"},
}


def _members_of(meta: dict, view: str) -> set:
    """Return every public member name of a view, unqualified.

    Args:
        meta (dict): A /meta response.
        view (str): The view name.

    Returns:
        set: The view's measure, dimension and segment names without the `<view>.` prefix.
    """
    return set(cube_client.short_names(
        cube_client.measures(meta, view) + cube_client.dimensions(meta, view)
        + cube_client.segments(meta, view)
    ))


def test_verticals_share_an_identical_inherited_core(meta: dict) -> None:
    """The part that comes from `extends` must be byte-identical across the three product lines.

    Asymmetry here means the same question ("quantos negócios ganhos?") answers differently
    depending on the line of business — exactly the drift `extends` exists to prevent.
    """
    sets = {v: _members_of(meta, f"operational_{v}") for v in VERTICALS}
    cores = {v: members - VERTICAL_SPECIFIC_MEMBERS[v] for v, members in sets.items()}
    reference_name, reference = next(iter(cores.items()))
    for name, core in cores.items():
        assert core == reference, (
            f"operational_{name} drifted from operational_{reference_name} in the INHERITED core: "
            f"only in {name}: {sorted(core - reference)}; "
            f"missing from {name}: {sorted(reference - core)}"
        )


@pytest.mark.parametrize("vertical", VERTICALS)
def test_vertical_specific_members_are_exactly_the_declared_ones(meta: dict, vertical: str) -> None:
    """The other half of the invariant: a line's own members are declared, not accidental.

    This is what keeps "vertical + specific" honest — an undeclared extra member means someone
    added a per-line concept without deciding whether the other lines need it too.
    """
    shared = set.intersection(*(_members_of(meta, f"operational_{v}") for v in VERTICALS))
    extras = _members_of(meta, f"operational_{vertical}") - shared
    assert extras == VERTICAL_SPECIFIC_MEMBERS[vertical], (
        f"operational_{vertical} specifics changed: expected "
        f"{sorted(VERTICAL_SPECIFIC_MEMBERS[vertical])}, got {sorted(extras)}"
    )


@pytest.mark.parametrize("vertical", VERTICALS)
def test_each_vertical_exposes_the_shared_deal_stage_measures(meta: dict, vertical: str) -> None:
    """The macro-generated won/lost/open trio plus the win rate exist in every vertical."""
    names = set(cube_client.short_names(cube_client.measures(meta, f"operational_{vertical}")))
    expected = {"won_deals_count", "lost_deals_count", "open_deals_count", "win_rate"}
    assert expected <= names, f"operational_{vertical} missing {sorted(expected - names)}"
