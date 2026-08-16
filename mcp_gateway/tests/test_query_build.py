r"""Unit tests for query building / param normalization (NO running Cube needed).

These guard the serialization fragility that broke date filters: a stringified array
(['a','b'] sent as the string "[\"a\",\"b\"]") must be parsed back, while relative
strings and real arrays pass through unchanged. Same hardening for filters, order and segments.
"""
from __future__ import annotations

from app.server import (
    Filter,
    _build_query,
    _filters_to_list,
    _norm_date_range,
    _norm_order,
    _norm_segments,
)


# --- date_range ---------------------------------------------------------------
def test_date_range_real_array_passthrough() -> None:
    """A genuine [start, end] array survives normalization unchanged."""
    assert _norm_date_range(["2024-01-01", "2026-12-31"]) == ["2024-01-01", "2026-12-31"]


def test_date_range_stringified_array_is_parsed() -> None:
    """The original bug: a client-serialized array must be parsed back to a real list."""
    assert _norm_date_range('["2024-01-01","2026-12-31"]') == ["2024-01-01", "2026-12-31"]


def test_date_range_relative_string_is_kept() -> None:
    """A relative range is a legitimate string and must NOT be parsed away."""
    assert _norm_date_range("last 30 days") == "last 30 days"
    assert _norm_date_range("this month") == "this month"


def test_date_range_none() -> None:
    """An absent range stays absent rather than becoming an empty value."""
    assert _norm_date_range(None) is None


# --- order --------------------------------------------------------------------
def test_order_dict_passthrough() -> None:
    """A real order object survives unchanged."""
    assert _norm_order({"sales.total_sale_amount": "desc"}) == {"sales.total_sale_amount": "desc"}


def test_order_stringified_is_parsed() -> None:
    """A client-serialized order object is parsed back."""
    assert _norm_order('{"sales.total_sale_amount":"desc"}') == {"sales.total_sale_amount": "desc"}


def test_order_garbage_string_dropped() -> None:
    """Unparseable input is dropped rather than forwarded to Cube as a broken order."""
    assert _norm_order("not-json") is None


# --- filters ------------------------------------------------------------------
def test_filters_from_models() -> None:
    """A typed Filter model serializes to the dict shape Cube expects."""
    f = [Filter(member="sales.product_line", operator="equals", values=["Furniture"])]
    assert _filters_to_list(f) == [
        {"member": "sales.product_line", "operator": "equals", "values": ["Furniture"]}
    ]


def test_filters_set_operator_omits_values() -> None:
    """The `set` operator takes no values — sending an empty list would be a different query."""
    f = [Filter(member="sales.rep_id", operator="set")]
    assert _filters_to_list(f) == [{"member": "sales.rep_id", "operator": "set"}]


def test_filters_stringified_dict_is_parsed() -> None:
    """Same serialization hazard as date_range, on the filters argument."""
    f = ['{"member":"sales.product_line","operator":"equals","values":["Furniture"]}']
    assert _filters_to_list(f) == [
        {"member": "sales.product_line", "operator": "equals", "values": ["Furniture"]}
    ]


# --- full query assembly ------------------------------------------------------
def test_build_query_puts_date_range_in_time_dimension() -> None:
    """The date range belongs inside timeDimensions, and must arrive there already parsed."""
    q = _build_query(
        ["sales.total_sale_amount"], None, "sales.sold_at", "month",
        '["2024-01-01","2026-12-31"]', None, None, 100,
    )
    td = q["timeDimensions"][0]
    assert td["dimension"] == "sales.sold_at"
    assert td["granularity"] == "month"
    assert td["dateRange"] == ["2024-01-01", "2026-12-31"]  # parsed, not a string
    assert q["limit"] == 100


# --- segments -----------------------------------------------------------------
def test_segments_real_list_passthrough() -> None:
    """A real segment list survives unchanged."""
    assert _norm_segments(["sales.new_customers"]) == ["sales.new_customers"]


def test_segments_stringified_array_is_parsed() -> None:
    """Same client-serialization hazard that broke date_range."""
    assert _norm_segments('["sales.new_customers","sales.base_reps_agencies"]') == [
        "sales.new_customers", "sales.base_reps_agencies"]


def test_segments_bare_string_becomes_single_item() -> None:
    """A single segment sent as a bare string is wrapped, not treated as JSON."""
    assert _norm_segments("sales.new_customers") == ["sales.new_customers"]


def test_segments_absent_is_empty() -> None:
    """Absent and empty both normalize to an empty list."""
    assert _norm_segments(None) == [] and _norm_segments([]) == []


def test_build_query_omits_segments_key_when_absent() -> None:
    """An empty `segments` array is not the same as no segments — Cube must not receive one."""
    q = _build_query(["sales.sales_count"], None, None, None, None, None, None, 10)
    assert "segments" not in q, "an absent segments arg must not send an empty array to Cube"


def test_build_query_includes_segments() -> None:
    """A stringified segment list reaches the query already parsed."""
    q = _build_query(["sales.sales_count"], None, None, None, None, None, None, 10,
                     '["sales.new_customers"]')
    assert q["segments"] == ["sales.new_customers"]  # parsed, not a string


def test_build_query_filters_and_order() -> None:
    """Filters and order land in the query untouched by the rest of the assembly."""
    q = _build_query(
        ["sales.total_sale_amount"], ["sales.product_line"], None, None, None,
        [Filter(member="sales.product_line", operator="equals", values=["Furniture"])],
        {"sales.total_sale_amount": "desc"}, 50,
    )
    assert q["filters"] == [
        {"member": "sales.product_line", "operator": "equals", "values": ["Furniture"]}
    ]
    assert q["order"] == {"sales.total_sale_amount": "desc"}
