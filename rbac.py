"""Single source of truth for the access-control model. Full design: docs/access-control.md.

Pure Python (no `import cube`) on purpose, so both the runtime config (cube.py) and, if needed, the
Jinja template context (model/globals.py) can import it. The model has two axes plus an override:
  - level    (ordinal int): table/view breadth. A view declares a min_level; visible if level >= it.
  - domains  (set of slugs): which business domains. On domain-specific views the domain gates
             visibility; on the transversal commercial views it becomes a row filter on
             product_line.
  - is_admin (bool override, outside the level scale): sees everything, ignores level and domain.

The wildcard domain "*" = all domains: every domain view is visible (still subject to level) and no
row filter applies — how "the leadership team sees everything" is granted without listing every
domain.
"""
from __future__ import annotations

# Wildcard meaning "all domains".
WILDCARD_DOMAIN = "*"

# Ordinal level scale, with GAPS so new tiers can be inserted later without
# renumbering. The names document the bands; the JWT carries the integer.
LEVELS = {
    "analyst": 10,
    "manager": 20,
    "director": 30,
    "executive": 40,
}

# Default minimum level required to see a view. Most views sit at 10 (anyone
# authenticated, subject to domain); finance/PII views are raised to 40.
DEFAULT_MIN_LEVEL = 10

# Business-domain slugs (horizontal axis): one per product line, plus suppliers.
# Mirrors the product lines exposed by the transversal cubes.
DOMAINS = ["appliances", "furniture", "electronics", "suppliers"]

# The dimension that carries the product line / domain on the transversal cubes and views.
CATEGORY_DIMENSION = "product_line"

# Domain slug -> product_line value(s) as stored in the warehouse.
# `suppliers` maps to no product line: supplier data lives in supplier-specific
# views, never in the transversal ones, so it grants no transversal rows.
DOMAIN_TO_CATEGORY = {
    "appliances": ["Appliances"],
    "furniture": ["Furniture"],
    "electronics": ["Electronics"],
    "suppliers": [],
}

# PUBLIC views that EXPOSE product_line, so the row filter can target
# `<view>.product_line`. These are the transversal commercial views.
# `customers` and `reps` are product-agnostic and intentionally excluded.
TRANSVERSAL_VIEWS_WITH_CATEGORY = [
    "sales",
    "commissions",
    "orders",
    "quotes",
    "funnel",
    "products",
]


def has_wildcard(domains: list[str] | None) -> bool:
    """True if the user holds the all-domains wildcard.

    Args:
        domains (list[str] | None): the domain slugs carried by the security context.

    Returns:
        bool: True when the wildcard is present.
    """
    return bool(domains) and WILDCARD_DOMAIN in domains


def domains_to_categories(domains: list[str] | None) -> list[str]:
    """Map domain slugs to the product_line values they unlock.

    Unknown slugs are ignored; the wildcard is NOT expanded here (callers must check has_wildcard
    first to skip filtering entirely).

    Args:
        domains (list[str] | None): the domain slugs carried by the security context.

    Returns:
        list[str]: A de-duplicated, order-stable list of product_line values.
    """
    out: list[str] = []
    for d in domains or []:
        for cat in DOMAIN_TO_CATEGORY.get(d, []):
            if cat not in out:
                out.append(cat)
    return out
