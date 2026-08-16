"""Reusable Python functions injected into the data models through Jinja.

Cube renders every .yml as a Jinja template and injects whatever is registered in this
TemplateContext. Use it for DRY BUSINESS RULES that repeat across cubes (filters, SQL fragments,
lists).

Do NOT confuse it with the root cube.py (runtime config: RLS/auth) nor with macros.jinja (YAML
block reuse, no logic). Rule of thumb:
    macros.jinja -> repeats a BLOCK of YAML
    globals.py   -> computes a VALUE (usually a SQL fragment) in Python
"""
from __future__ import annotations

from cube import TemplateContext

template = TemplateContext()

# Optional prefix in front of <dataset>.<table>. Empty for the default DuckDB target, whose
# schemas live directly in the attached database. Point a fork at BigQuery by setting this to
# "`your-project`" — one line changes the whole model, which is why table() exists at all.
WAREHOUSE = ""

# Product lines exposed by the transversal cubes. Mirrors rbac.DOMAIN_TO_CATEGORY
# values — the RLS row filter targets exactly these strings.
PRODUCT_LINES = ["Appliances", "Furniture", "Electronics"]


@template.function
def table(dataset: str, name: str) -> str:
    """Build a fully-qualified warehouse table reference.

    Centralizing this means a fork repoints every cube by editing WAREHOUSE only.

    Args:
        dataset (str): the warehouse dataset (e.g. "core_sales").
        name (str): the table name inside that dataset.

    Returns:
        str: The table reference for `sql_table`.
    """
    return f"{WAREHOUSE}.{dataset}.{name}" if WAREHOUSE else f"{dataset}.{name}"


@template.function
def valid_rows(t: str, date_col: str = "created_at") -> str:
    """Build the reusable "valid rows" SQL predicate.

    Centralizes a business rule shared by several cubes — internal test accounts are excluded and
    a history cutoff is applied, so every cube that opts in counts the same universe. Usage in .yml:
    `sql: "SELECT * FROM ... WHERE {{ valid_rows('t') }}"`.

    Args:
        t (str): the table/alias used to qualify the columns.
        date_col (str): the date column the cutoff applies to.

    Returns:
        str: A SQL boolean expression for a WHERE clause.
    """
    return (
        f"{t}.rep_id NOT IN (SELECT rep_id FROM {WAREHOUSE}.core_rep.test_accounts) "
        f"AND {t}.{date_col} >= TIMESTAMP('2022-01-01')"
    )


@template.function
def canonical_order_filter(t: str) -> str:
    """The CANONICAL order-counting rule, in one place.

    An order counts when it is a ROOT order (no parent — child rows are re-submissions of the same
    deal) and is not a SELF-PURCHASE (the rep buying for themselves). Every measure that claims to
    count "orders generated" must apply exactly this, or the numbers stop reconciling across views.

    Args:
        t (str): the table/alias used to qualify the columns.

    Returns:
        str: A SQL boolean expression for a WHERE clause.
    """
    return f"{t}.parent_order_id IS NULL AND {t}.customer_id != {t}.rep_id"
