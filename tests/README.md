# tests/ — semantic-layer test suite

These tests treat the semantic layer as a **contract**. They do not re-test data quality (that
belongs upstream, in whatever builds the marts); they test that the model **compiles**, that it
**exposes exactly the governed members it should**, that **access control actually holds**, that
joins **do not fan out**, and — when a warehouse is available — that the numbers **match the
source**.

The suite drives a **running Cube instance** over its REST API.

```
tests/
  cube_client.py     # API client + meta helpers (mint_jwt, get_meta_as, short_names, …)
  conftest.py        # fixtures: meta, load, warehouse_sql
  affected_views.py  # maps a diff to the views it touches, for selective smoke runs
  contract/
    test_contract_core.py    # compiles, storefront is closed, cubes stay private, naming
    test_governance.py       # descriptions, non-additive flags, folders, PII confinement
    test_access_policy.py    # RBAC on both axes + the row filter
    test_cube_features.py    # every Cube feature the repo demonstrates
  test_smoke.py              # every measure/segment/time dimension answers
  test_fanout.py             # joins do not inflate additive totals
  test_parity.py             # the layer's number IS the source's number
```

## Test tiers (pytest markers)

| Marker | Tests | Needs | What it covers |
|---|---|---|---|
| `contract` | 183 | Cube up | compile, storefront closed, cubes `public:false`, docs quality, PII confinement, **RBAC + RLS**, and every modelling feature |
| `smoke` | 66 | Cube up **+ warehouse** | every public measure, segment and time dimension answers |
| `fanout` | 8 | Cube up **+ warehouse** | a joined dimension never inflates an additive total |
| `warehouse` | 17 | warehouse | **parity** against the source, plus PK uniqueness |

**Why the parity tier earns its place.** Every other tier proves the layer is well-FORMED —
members exist, are documented, are gated, are queryable, do not fan out. None proves any of them is
CORRECT. Parity is the only tier that checks the promise a semantic layer actually makes: one
definition, the same answer, and the right one. It also pins the *reasons* behind the modelling —
`test_summing_commission_at_installment_grain_over_counts` fails if the source ever stops
repeating the amount per installment, which is exactly when the commission routing should be
revisited.

**The `contract` tier needs no warehouse at all** — 183 checks, all of them meaningful:

- **Visibility** is metadata. `/meta` is compiled *per security scope*, so asking for it as a given
  persona returns exactly what that persona may see.
- **The row filter** is proven by reading the SQL Cube **generates** (via `/v1/sql`) rather than by
  executing it. That is enough to assert a domain analyst is confined to their product line, that a
  domain mapping to no product line **fails closed**, and that a **segment cannot bypass** RLS.
- **Pre-aggregation routing** is likewise visible in the generated SQL: a matching query must name
  the rollup table instead of the source table.

This is why the interesting half of the suite runs in CI against a Cube with no credentials.

## Running

```bash
# 1. bring the model up
docker compose up --build -d

# 2. install test deps
pip install -r tests/requirements.txt

# 3a. contract tier — needs no warehouse, good for every PR
pytest -m contract

# 3b. everything (the other tiers skip cleanly when the warehouse is absent)
pytest
```

`conftest.py` loads the repo's `.env` for any variable not already set, so the personas are signed
with the same `CUBEJS_API_SECRET` the running container uses and the commands above work with no
exports. Real environment variables win, so CI can override without touching the file.

Config: `CUBE_URL` (default `http://localhost:4000`), `CUBEJS_API_SECRET` (mints the per-persona
JWTs) and `CUBE_API_TOKEN`. The warehouse tier reads `warehouse/meridian.duckdb` directly — build
it with `python warehouse/seed/load_duckdb.py` and the tier stops skipping.

> The parity fixture queries a **copy** of the database. DuckDB takes an exclusive lock and Cube
> holds the file open for the whole session, so opening the original fails even read-only.

> **Never run the access tier with `CUBEJS_DEV_MODE=true`.** Dev mode ignores access policies
> entirely, so every assertion in `test_access_policy.py` would pass vacuously.

## Two things that make these tests honest

**Compare unqualified names.** `/meta` qualifies every member (`sales.win_rate`). Comparing a bare
name against a qualified one matches nothing — which turns "does this view leak PII?" into a test
that passes because it checked nothing. Use `cube_client.short_names()` whenever comparing against
literal member names; that bug was live in this suite and is the reason the helper exists.

**Assert both halves of an invariant.** The verticals test does not just check that the three
pipelines share a core — it also checks that each one's *specific* members are exactly the declared
ones. Only asserting the first half lets a per-line concept get added without anyone deciding
whether the other lines need it too.

## Selective smoke

`SMOKE_ONLY_VIEWS` narrows the `smoke` tier to the views a diff actually touches (`ALL` / `NONE` /
a comma-separated list); `affected_views.py` computes it. The `contract`, `fanout` and `warehouse`
tiers always run in full.
