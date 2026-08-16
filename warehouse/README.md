# warehouse/ — the schema the model expects, and data to fill it

The semantic layer describes tables; this directory **produces** them. Both the DDL and the seed
are generated, never hand-written, so they cannot drift from the cubes that reference them.

```
warehouse/
├── build_schema.py     # derives the schema FROM model/cubes/**
├── schema.json         # machine-readable output (table -> column -> type)
├── ddl/schema.sql      # CREATE SCHEMA + CREATE TABLE, generated
├── Dockerfile          # the compose `seed` service that runs before Cube
└── seed/
    ├── generate.py     # deterministic data generator -> data/*.csv
    └── load_duckdb.py  # builds warehouse/meridian.duckdb from the two above
```

Nothing here is versioned except the **schema** (`ddl/schema.sql`, `schema.json`) and the code
that produces it. The CSVs and the `.duckdb` file are generated: bulk output of a deterministic
generator, and a binary in git costs a fresh multi-megabyte blob on every regeneration —
permanently, since history never shrinks.

## Why generated

Hand-writing 23 tables and 213 columns guarantees drift: someone renames a dimension's column and
the DDL keeps the old name. The break then surfaces at query time, against a warehouse — the most
expensive place to find it.

`build_schema.py` renders the model's Jinja **exactly as Cube does** (macros expanded, `extends`
resolved, interpolated scalars JSON-quoted) and collects every column each cube touches, including
the ones inside expressions, joins and segment predicates. It also fails loudly when the same
column is inferred with **different types in different tables** — a join key typed `STRING` on one
side and `NUMERIC` on the other compiles fine and then silently matches nothing.

```bash
python warehouse/build_schema.py     # after any model change
python warehouse/seed/generate.py    # regenerate the CSVs
```

## Why the seed is deterministic

Same seed in, byte-identical CSVs out. That is what lets a parity test assert on a number and keep
asserting on it across runs and machines.

## What the data deliberately gets "wrong"

A demo where every column is populated and every join is 1:1 teaches the opposite of what a
semantic layer is for. This seed reproduces the traps the model documents:

| Trap in the data | What it makes demonstrable |
|---|---|
| `commission_ledger` repeats the sale's full commission on every installment row | a naive `SUM` there over-counts **4.26×**; the canonical total lives at sale grain |
| ~12% of orders are children, ~3% are self-purchases | `orders_count` (canonical) genuinely differs from `all_orders_count` |
| `lost_reason` is NULL unless the deal was lost; `closed_at` is NULL while open | NULL as *semantics*, not as a gap — what `meta.coverage` declares |
| the journey drops off between stages | conversion rates are real numbers, not 100% |
| some reps never logged in; some quotes never converted | absence of a row ≠ non-existence of the entity |
| one charge can span several invoice rows | `is_primary_charge_row` exists for a reason |

## Building it

`docker compose up` does this for you — the `seed` service runs first and Cube waits for it. To
build it by hand:

```bash
pip install -r warehouse/requirements.txt
python warehouse/seed/load_duckdb.py            # skips work already done
python warehouse/seed/load_duckdb.py --force    # rebuild from scratch
```

Result: `warehouse/meridian.duckdb`, 23 tables, ~90k rows, ~7.6 MB.

## Pointing it at another warehouse

The default target is DuckDB, because a demo nobody can run teaches nothing. Retargeting takes two
edits:

1. `model/globals.py` › `WAREHOUSE` — set your project prefix (e.g. `` "`your-project`" `` for
   BigQuery). `table()` exists precisely so this is one line for the whole model.
2. `.env` › the `CUBEJS_DB_*` block for your engine.

Column types are deliberately plain (`STRING`, `NUMERIC`, `TIMESTAMP`, `BOOL`) and load as-is on
BigQuery and DuckDB; on Postgres map `STRING`→`TEXT` and `BOOL`→`BOOLEAN`. The CSVs under
`seed/data/` are a neutral format precisely so another engine can be loaded without exporting
anything back out of DuckDB.

The only dialect-specific SQL in the model is `date_diff('day', a, b)`, in
`_base/base_deal_pipeline.yml` and `network/base_leads.yml`. On BigQuery that becomes
`TIMESTAMP_DIFF(b, a, DAY)` — note the **argument order inverts**, and getting it backwards
silently flips the sign of every duration.
