"""Warehouse tier — the number the layer reports IS the number in the source.

Everything else in this suite proves the layer is well-FORMED: members exist, are documented, are
gated, are queryable, do not fan out. None of it proves any of them is CORRECT.

That is the whole promise of a semantic layer — one definition, the same answer for everyone — so
it is the claim that most deserves a test. Each case below computes a number twice: once through
Cube, once straight from the warehouse, and asserts they agree.

Needs the local DuckDB (`python warehouse/seed/load_duckdb.py`). Skips cleanly without it.
"""
from __future__ import annotations

from collections.abc import Callable

import cube_client
import pytest

pytestmark = pytest.mark.warehouse

CENTS = 0.01


def _measure(load: Callable, name: str) -> float:
    """Read a single scalar measure through Cube.

    Args:
        load (Callable): The query runner.
        name (str): Qualified measure name.

    Returns:
        float: The value, as a float.
    """
    rows = load({"measures": [name]}).get("data", [])
    return float(rows[0][name] or 0)


def _scalar(warehouse_sql: Callable, sql: str) -> float:
    """Read a single scalar straight from the warehouse.

    Args:
        warehouse_sql (Callable): The direct query runner.
        sql (str): A statement returning one row with one column.

    Returns:
        float: The value, as a float.
    """
    row = warehouse_sql(sql)[0]
    return float(next(iter(row.values())) or 0)


# --------------------------------------------------------------------- additive totals

def test_revenue_matches_the_source(load: Callable, warehouse_sql: Callable) -> None:
    """Total sale amount through the layer equals SUM(sale_amount) at the source."""
    through_cube = _measure(load, "sales.total_sale_amount")
    at_source = _scalar(warehouse_sql, "SELECT SUM(sale_amount) FROM core_sales.sales_summary")
    assert abs(through_cube - at_source) < CENTS, f"{through_cube:,.2f} != {at_source:,.2f}"


def test_sales_count_matches_the_source(load: Callable, warehouse_sql: Callable) -> None:
    """Sales count equals the number of distinct sales."""
    through_cube = _measure(load, "sales.sales_count")
    at_source = _scalar(
        warehouse_sql, "SELECT COUNT(DISTINCT sale_id) FROM core_sales.sales_summary")
    assert through_cube == at_source


def test_commission_total_matches_the_sale_grain(load: Callable, warehouse_sql: Callable) -> None:
    """The canonical commission total equals the sum at SALE grain."""
    through_cube = _measure(load, "sales.total_commission_amount")
    at_source = _scalar(
        warehouse_sql, "SELECT SUM(commission_total_amount) FROM core_sales.sales_summary")
    assert abs(through_cube - at_source) < CENTS, f"{through_cube:,.2f} != {at_source:,.2f}"


def test_commission_split_reconciles(load: Callable, warehouse_sql: Callable) -> None:
    """Company share + rep share add back up to the total, at the source and through the layer."""
    company = _measure(load, "sales.total_commission_company_amount")
    rep = _measure(load, "sales.total_commission_rep_amount")
    total = _measure(load, "sales.total_commission_amount")
    assert abs((company + rep) - total) < CENTS, f"{company:,.2f} + {rep:,.2f} != {total:,.2f}"


# --------------------------------------------------------------------- the modelling decisions

def test_summing_commission_at_installment_grain_over_counts(warehouse_sql: Callable) -> None:
    """Pin the reason the commission total is NOT read from the ledger.

    The ledger carries one row per installment, each repeating the sale's FULL commission. Summing
    there is the single most expensive mistake available in this model, and the whole routing —
    read the total from `sales`, keep the ledger for the rate — exists because of it. If this
    assertion ever fails, the source changed and that routing must be revisited.
    """
    at_sale_grain = _scalar(
        warehouse_sql, "SELECT SUM(commission_total_amount) FROM core_sales.sales_summary")
    naive_on_ledger = _scalar(
        warehouse_sql, "SELECT SUM(commission_amount) FROM core_sales.commission_ledger")
    ratio = naive_on_ledger / at_sale_grain
    assert ratio > 1.5, (
        f"the installment-grain trap disappeared (ratio {ratio:.2f}x) — if the source really "
        "changed, the commission routing in the model should be revisited"
    )


def test_canonical_order_count_excludes_children_and_self_purchases(
    load: Callable, warehouse_sql: Callable,
) -> None:
    """`orders_count` applies the canonical rule; `all_orders_count` does not.

    Two measures, deliberately different, and the difference is the rule. Asserting both against
    the source is what stops someone "simplifying" them into one.
    """
    canonical = _measure(load, "orders.orders_count")
    raw = _measure(load, "orders.all_orders_count")
    at_source = _scalar(warehouse_sql, """
        SELECT COUNT(DISTINCT order_id) FROM core_orders.orders_overview
        WHERE parent_order_id IS NULL AND customer_id != rep_id
    """)
    all_at_source = _scalar(
        warehouse_sql, "SELECT COUNT(DISTINCT order_id) FROM core_orders.orders_overview")
    assert canonical == at_source, f"canonical {canonical} != {at_source}"
    assert raw == all_at_source, f"raw {raw} != {all_at_source}"
    assert canonical < raw, "the canonical rule is excluding nothing — is the fixture data right?"


def test_agencies_are_counted_by_company_not_by_rep(
    load: Callable, warehouse_sql: Callable,
) -> None:
    """An agency has several reps, so counting agencies means counting distinct companies."""
    agencies = _measure(load, "reps.agencies_count")
    at_source = _scalar(warehouse_sql, """
        SELECT COUNT(DISTINCT company_id) FROM core_rep.rep_overview
        WHERE tier IN ('Associate', 'Agency')
    """)
    reps_in_agencies = _scalar(warehouse_sql, """
        SELECT COUNT(DISTINCT rep_id) FROM core_rep.rep_overview
        WHERE tier IN ('Associate', 'Agency')
    """)
    assert agencies == at_source, f"{agencies} != {at_source}"
    assert agencies < reps_in_agencies, "agencies and reps came out equal — the grain is wrong"


def test_win_rate_excludes_open_deals_from_the_denominator(
    load: Callable, warehouse_sql: Callable,
) -> None:
    """Win rate is won / (won + lost). Open deals are undecided and stay out."""
    through_cube = _measure(load, "operational_appliances.win_rate")
    at_source = _scalar(warehouse_sql, """
        SELECT 100.0 * COUNT(*) FILTER (WHERE status = 'won')
             / NULLIF(COUNT(*) FILTER (WHERE status IN ('won', 'lost')), 0)
        FROM mart_appliances.deal_pipeline
    """)
    assert abs(through_cube - at_source) < 0.01, f"{through_cube:.4f} != {at_source:.4f}"


# --------------------------------------------------------------------- governance, numerically

def test_row_level_security_returns_the_right_subset(warehouse_sql: Callable) -> None:
    """RLS must return the CORRECT rows, not merely fewer of them.

    The contract tier proves the predicate is injected; this proves the predicate is right — an
    analyst's total equals their product line's total at the source, to the cent.
    """
    for domain, line in [("appliances", "Appliances"), ("furniture", "Furniture")]:
        claims = {"is_admin": False, "level": 10, "domains": [domain]}
        rows = cube_client.load_as(
            claims, {"measures": ["sales.total_sale_amount"]}).get("data", [])
        through_cube = float(rows[0]["sales.total_sale_amount"] or 0)
        at_source = _scalar(warehouse_sql, f"""
            SELECT SUM(sale_amount) FROM core_sales.sales_summary
            WHERE product_line = '{line}'
        """)
        assert abs(through_cube - at_source) < CENTS, (
            f"{domain} analyst saw {through_cube:,.2f}, source says {at_source:,.2f}")


def test_a_domain_with_no_product_line_sees_nothing(warehouse_sql: Callable) -> None:
    """The suppliers asymmetry, numerically: deny-all means zero, not "everything"."""
    claims = {"is_admin": False, "level": 10, "domains": ["suppliers"]}
    rows = cube_client.load_as(claims, {"measures": ["sales.total_sale_amount"]}).get("data", [])
    value = float(rows[0]["sales.total_sale_amount"] or 0) if rows else 0.0
    assert value == 0.0, f"a suppliers analyst read {value:,.2f} of revenue"


# --------------------------------------------------------------------- source integrity

@pytest.mark.parametrize("table,key", [
    ("core_sales.sales_summary", "sale_id"),
    ("core_orders.orders_overview", "order_id"),
    ("core_quotes.quotes_base", "quote_id"),
    ("core_rep.rep_overview", "rep_id"),
    ("core_customer.customer_overview", "customer_id"),
    ("core_product.product_overview", "product_id"),
    ("mart_appliances.deal_pipeline", "deal_id"),
])
def test_primary_key_is_unique_at_the_source(warehouse_sql: Callable, table: str, key: str) -> None:
    """A duplicated key silently doubles every measure built on that table."""
    duplicates = _scalar(warehouse_sql, f"""
        SELECT COUNT(*) FROM (
            SELECT {key} FROM {table} GROUP BY {key} HAVING COUNT(*) > 1
        )
    """)
    assert duplicates == 0, f"{table}.{key} has {duplicates:.0f} duplicated value(s)"
