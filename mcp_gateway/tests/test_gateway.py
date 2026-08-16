"""Integration tests for the MCP gateway tools.

Two tiers, by what each one needs:
  `live_client`      — Cube compiled and answering /meta. Covers every METADATA tool, which is
                       what decides whether the agent can find and correctly read a metric.
  `warehouse_client` — additionally needs a real warehouse behind Cube; skips without one.
"""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from app import server as srv
from app.cube_client import CubeClient


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine to completion on the session event loop.

    Args:
        coro (Coroutine[Any, Any, Any]): The awaitable returned by a gateway tool.

    Returns:
        Any: Whatever the coroutine resolved to.
    """
    return asyncio.get_event_loop().run_until_complete(coro)


# --------------------------------------------------------------- discovery & governance

def test_list_metrics_returns_only_views(live_client: CubeClient) -> None:
    """Discovery returns the public storefront only — a private cube must never surface here."""
    views = _run(srv.list_metrics())
    assert views, "expected at least one public view"
    # Governance: private cubes (base_*) must never surface as views.
    assert all(not v.name.startswith("base_") for v in views)
    assert any(v.name == "sales" for v in views)


def test_describe_view_has_members(live_client: CubeClient) -> None:
    """A described view carries real members, not just a name."""
    detail = _run(srv.describe_view("sales"))
    assert detail.measures and detail.dimensions
    assert "sales.total_sale_amount" in {m.name for m in detail.measures}


def test_validate_query_rejects_unknown_member(live_client: CubeClient) -> None:
    """A dry run catches an invented member before it reaches the warehouse."""
    res = _run(srv.validate_query(measures=["sales.nonexistent_measure"]))
    assert res.valid is False and res.error


# --------------------------------------------------------------- metadata round trips

def test_describe_view_forwards_view_level_meta(live_client: CubeClient) -> None:
    """Guard the view-level `meta` round trip: YAML -> /meta -> ViewDetail.

    The gateway models used to drop view-level `meta`, so `meta.ai_context` written on a view
    never reached the agent.

    Guard the whole round trip: YAML -> /meta -> ViewDetail.
    """
    detail = _run(srv.describe_view("commissions"))
    assert detail.meta, "view-level meta must be forwarded — the agent reads meta.ai_context"
    ctx = (detail.meta.get("ai_context") or "").strip()
    assert ctx, "commissions must expose meta.ai_context"
    # The prose must survive intact: this view's core rule is the installment-grain trap.
    assert "parcela" in ctx.lower()


def test_describe_view_forwards_member_level_meta(live_client: CubeClient) -> None:
    """Check that member-level meta keeps its structured keys.

    additive/avoid/synonyms are what stop the agent summing something it must not sum.
    """
    detail = _run(srv.describe_view("sales"))
    by_name = {m.name: m for m in detail.measures}
    m = by_name["sales.total_commission_amount"]
    assert m.meta and m.meta.get("additive") is True


def test_non_additive_measure_is_flagged_through_the_gateway(live_client: CubeClient) -> None:
    """The other half of the same guard: a ratio must arrive flagged, or the agent will sum it."""
    detail = _run(srv.describe_view("sales"))
    by_name = {m.name: m for m in detail.measures}
    assert by_name["sales.revenue_share_of_line"].meta.get("additive") is False


def test_list_metrics_exposes_segments(live_client: CubeClient) -> None:
    """Segments must appear in discovery, or the agent hand-builds filters that already exist."""
    views = {v.name: v for v in _run(srv.list_metrics())}
    assert "sales.new_customers" in views["sales"].segments
    assert "reps.active_reps" in views["reps"].segments


def test_describe_view_forwards_folders(live_client: CubeClient) -> None:
    """Check that folders survive the round trip.

    Folders are the thematic map of a wide view — without them the agent reads a flat 100+ member
    list.

    Checks the round trip on `subscription`, including that every folder member is a real member of
    the view.
    """
    detail = _run(srv.describe_view("subscription"))
    assert detail.folders, "subscription must expose folders"
    by_name = {f.name: f for f in detail.folders}
    assert "Pagante (PII)" in by_name, "the PII folder must survive the trip"
    all_members = {m.name for m in detail.measures + detail.dimensions + detail.segments}
    for f in detail.folders:
        unknown = set(f.members) - all_members
        assert not unknown, f"folder '{f.name}' references unknown members: {unknown}"


def test_describe_view_segments_carry_their_definition(live_client: CubeClient) -> None:
    """A segment without a description is an unexplained filter the agent cannot judge."""
    detail = _run(srv.describe_view("reps"))
    by_name = {s.name: s for s in detail.segments}
    seg = by_name["reps.agencies"]
    assert (seg.description or "").strip(), "a segment with no description is an unexplained filter"
    # The style guide requires stating what falls OUTSIDE the cut.
    assert "FICA FORA" in seg.description.upper() or "FICAM FORA" in seg.description.upper()


# --------------------------------------------------------------- execution (needs a warehouse)

def test_run_query_returns_data_and_sql(warehouse_client: CubeClient) -> None:
    """A query returns rows AND the SQL that produced them, so an answer can always be audited."""
    res = _run(srv.run_query(
        measures=["sales.total_sale_amount"],
        dimensions=["sales.product_line"],
        limit=10,
    ))
    assert res.row_count >= 0
    assert res.sql and "select" in res.sql.lower()


def test_run_query_with_segment_filters_and_does_not_fan_out(warehouse_client: CubeClient) -> None:
    """Check end to end that a segment filters the fact instead of multiplying it.

    The `segments` param must reach Cube, and a segment on a JOINED cube (base_reps) must not
    fan out the rows it travels through.
    """
    total = _run(srv.run_query(measures=["sales.sales_count"])).data[0]["sales.sales_count"]
    seg = _run(srv.run_query(measures=["sales.sales_count"],
                             segments=["sales.base_reps_active_reps"]))
    value = seg.data[0]["sales.sales_count"]
    assert seg.query["segments"] == ["sales.base_reps_active_reps"]
    assert 0 < float(value) <= float(total), (
        f"segment must filter, not multiply: {total} -> {value}")


# --- date_range regression (the bug: a stringified array collapsed to 1 row) --------------
def test_run_query_date_range_array_multi_month(warehouse_client: CubeClient) -> None:
    """A multi-year monthly range must return several months, not collapse to one row."""
    res = _run(srv.run_query(
        measures=["sales.total_sale_amount"],
        time_dimension="sales.sold_at",
        granularity="month",
        date_range=["2024-01-01", "2026-12-31"],
    ))
    assert res.row_count > 1, "a multi-year monthly range must return several months"


def test_run_query_date_range_stringified_is_normalized(warehouse_client: CubeClient) -> None:
    """The same range sent as a serialized string must behave identically.

    Even if the MCP client serializes the array as a string, the gateway must normalize it.
    """
    res = _run(srv.run_query(
        measures=["sales.total_sale_amount"],
        time_dimension="sales.sold_at",
        granularity="month",
        date_range='["2024-01-01","2026-12-31"]',
    ))
    assert res.row_count > 1, "stringified date_range must be parsed back to a real range"
