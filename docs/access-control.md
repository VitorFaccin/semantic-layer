# Access control — RBAC by level + domain, plus row-level security

How the semantic layer decides **which views** a caller may see and **which rows** they get back.
Two axes plus an override, carried in a signed JWT, enforced by Cube's native `access_policy`
(view visibility) and by `query_rewrite` (row filter).

Single source of truth for the model: [`rbac.py`](../rbac.py). Runtime wiring: [`cube.py`](../cube.py).

---

## The two axes

| Axis | Carried as | Governs | Enforced by |
|---|---|---|---|
| **level** | `level: 10\|20\|30\|40` | **breadth** — a view declares a minimum level | `access_policy` group `t<level>` |
| **domains** | `domains: ["appliances", ...]` | **which business domains** | `access_policy` group `d_<domain>_<level>` on domain views; **row filter** on transversal views |
| **is_admin** | `is_admin: true` | override — sees everything | short-circuits both |

The wildcard `domains: ["*"]` means all domains: every domain view becomes visible (still subject
to level) and **no row filter applies**. That is how leadership is granted broad access without
enumerating every domain.

### The level scale

Deliberately spaced, so a new tier can be inserted without renumbering:

| Level | Name | Sees |
|---|---|---|
| 10 | analyst | transversal + own domain views |
| 20 | manager | same, room for future gating |
| 30 | director | same, room for future gating |
| 40 | executive | adds the finance/PII views (`reps`, `subscription`, `renewals`) |

---

## How a JWT becomes permissions

```
JWT  →  check_auth      →  security context {level, domains, is_admin}
        context_to_groups →  ["t10","t20","d_appliances_10", ...]   → access_policy (VISIBILITY)
        query_rewrite     →  product_line IN (...)                  → row filter (ROWS)
        context_to_app_id →  one compiled model + cache per scope   → isolation
```

**Why the groups are computed in Python.** Cube's `access_policy` `condition` parser is limited
(no comparisons, no `in`, no chained or). So all the logic — level bands AND domain expansion —
runs in real Python in `context_to_groups`, and each view's policy matches on a **group name
alone**, with no conditions. Transversal views use the `access_policy_transversal(min_level)`
macro (`t10`, `t40`); domain views inline their group (`d_appliances_10`) because Cube would
JSON-quote an interpolated slug.

**Why the row filter is separate.** Visibility is per view; rows are per query. `query_rewrite`
injects `product_line IN (<the lines the caller's domains unlock>)` on every transversal view the
query touches. A non-admin whose domains map to no product line gets a **deny-all** filter, never
an unfiltered result — failure closes.

---

## The asymmetry worth knowing

`suppliers` is a domain that maps to **no product line** (`rbac.DOMAIN_TO_CATEGORY`). A supplier
analyst therefore sees `supplier_performance` in full but gets **zero rows** from `sales`,
`orders` or `commissions`. That is intentional: supply-side access should not leak revenue.

## What is gated where

| View group | Min level | Domain-gated |
|---|---|---|
| `sales` `orders` `quotes` `commissions` `funnel` `products` | 10 | row filter on `product_line` |
| `customers` `rep_activity` `growth` `logins` `leads` `lead_funnel` | 10 | no (product-agnostic) |
| `operational_*` `ranking_rep_*` | 10 | yes, visibility by domain group |
| `supplier_performance` | 10 | yes (`suppliers` domain) |
| `reps` `subscription` `renewals` | **40** | no — the level floor is the gate (PII + finance) |

## PII policy

Rep PII (`email`, `phone`, `tax_id`) is declared **only** on the `reps` view; payer PII
(`payer_name`, `payer_tax_id`) only on `subscription`. Fact views never carry PII — the standard
rep attribute block (`macros.jinja`) deliberately excludes it, so the gate is applied **once**,
where the data lives, instead of being re-checked on every fact view.

## Testing it

`tests/contract/test_access_policy.py` mints one JWT per persona and asserts both axes: which
views each persona sees, and that a domain analyst's rows are confined to their product line —
including that a **segment cannot bypass** the row filter.

> **Local caveat:** with `CUBEJS_DEV_MODE=true` Cube ignores access policies and everything is
> visible. The compose file ships with dev mode **off** for that reason — turning it on silently
> disables every assertion in the access tier.
