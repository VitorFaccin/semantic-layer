# CLAUDE.md — working in this repository

How to **work** in this repo. What it *is* — the architecture, the Cube feature map, how to run it,
the test tiers — lives in [`README.md`](README.md); read that first and do not restate it here.

## Orientation

A reference Cube Core semantic layer over a **local DuckDB warehouse**, built on `docker compose
up` from a model-derived schema and a deterministic seed — no cloud, no credentials. The
`contract` test tier still needs no warehouse at all (visibility is metadata; the row filter is
proven by reading generated SQL), which is what keeps it runnable anywhere.

The architecture is the author's own design, rebuilt here on a fictional domain with
synthetic data — see README › *On authorship*. Nothing in this repo comes from anywhere else.

**The one rule:** `cubes/` are private, only `views/` are public. A column outside a view does not
exist for the consumer. Do not weaken this for convenience — it is what makes the rest of the
governance enforceable instead of advisory.

## Reading order

Follow the execution flow:

1. **`README.md`** — what the project is and the feature map.
2. **`rbac.py`** then **`cube.py`** — the gatekeeper: who you are, what you may see, which rows.
3. **`docs/access-control.md`** — the design behind those two files.
4. **`model/cubes/_base/base_deal_pipeline.yml`** — the abstract cube, and why `extends` beats a macro.
5. **`model/cubes/commercial/base_sales.yml`** — the richest cube: pre-aggregations, multi-stage
   measures, rolling window, custom granularities.
6. **`model/macros.jinja`** + **`model/globals.py`** — the two reuse mechanisms and when each applies.
7. **`model/views/commercial/sales.yml`** — what a consumer sees, including nested folders.
8. **`tests/contract/test_cube_features.py`** — every feature above, pinned.

## Decisions not to reverse silently

- **Models in YAML**, config in Python (`cube.py`). YAML is not the weaker option — rolling
  windows, multi-stage and cumulative measures are all declarative.
- **Image version always pinned** (never `:latest`), and `CUBEJS_DEV_MODE=false`.
- **`rbac.py` is the single source of truth** for levels, domains and the domain→product-line map.
  Adding a domain or a level means editing that file, then adding a persona to
  `tests/contract/test_access_policy.py` — never hardcoding a group name in a view.
- **Transversal vs vertical is load-bearing.** Transversal views span product lines and are cut by
  the **row filter**; vertical views exist once per line and are cut by **visibility**. A new
  metric belongs to one or the other — decide which before writing it.

## Writing conventions

### Python

Codified in [`ruff.toml`](ruff.toml), so `ruff check .` is the arbiter rather than memory:

- **Everything in English** — names, comments, docstrings. 100-column lines.
- **Every function is fully typed**, parameters and return, two-line helpers included.
  `from __future__ import annotations` at the top of every module.
- **Every function has a Google docstring** carrying the types: `name (type): explanation` in
  Args, `type: explanation` in Returns, one line each. The first line is imperative and
  self-sufficient. No Args block when there are no arguments; no Returns block when it returns
  `None`; generators document `Yields` instead.
- **A module-internal function is prefixed `_`.**
- **Comments explain WHY** — the business rule, the decision, the incident. Never the obvious what.

One deliberate exception: pytest test functions are typed and documented, but do **not** repeat an
`Args:` block for fixtures. The fixture is pytest machinery, its type is already in the signature,
and describing `meta (dict)` identically on forty tests is noise, not documentation. Encoded as a
per-file ignore in `ruff.toml`.

### Model

- Cubes are `base_*`, private, one per source table, in a subfolder per business area. Views take
  the business term, plural. `.yml`, 2-space indentation, `snake_case` members.
- **Reuse, in order of preference:** `extends` (shared member *definitions*) → `macros.jinja`
  (repeated YAML blocks) → `globals.py` (business rules as Python). `extends` creates a real
  parent/child relation Cube understands; a macro only copies text. Full guidance in
  [`docs/semantic-layer-patterns.md`](docs/semantic-layer-patterns.md).
- **Language split:** READMEs, comments and docstrings in **English**. Only the Cube member
  `description` / `title` / `meta` — the business semantics an agent relays to a user — in
  **Portuguese**.
- **Descriptions follow [`docs/description-style-guide.md`](docs/description-style-guide.md).**
  Lead with the canonical term and its definition, disambiguate from siblings ("para X use
  `{outra_measure}`"), state grão · unidade · aditividade · escopo, one synonym sentence, no
  keyword salad. **Structure before prose:** a fact that fits a `meta` key or a `segment` goes
  *there*, never duplicated as text. `meta.ai_context` is the last resort.
- **Flag non-additive measures** (`meta.additive: false`). An unflagged ratio is the classic agent
  error — it sums percentages across a breakdown and reports an impossible number.
- **PII stays on its owning view** (`reps`, `subscription`), behind the level-40 floor. Fact views
  never carry it, so the gate is applied once where the data lives.

## Gotchas

- **Cube JSON-quotes any interpolated Jinja value.** Write `sql_table: {{ table(...) }}`, never
  `sql_table: "{{ table(...) }}"` — the latter renders `""dataset.table""` and the YAML dies. Same
  for `title:` / `description:` inside macros: build composite strings with `{% set %}` first.
- **A duplicated YAML key passes `yaml.safe_load` and then breaks Cube.** PyYAML keeps the last
  duplicate; the `js-yaml` Cube uses raises `duplicated mapping key` and the *whole* model fails to
  compile (HTTP 500 on `/meta`). Editing `meta:` blocks with a script is the usual way to introduce
  one.
- **`/meta` reports views, not cubes** — `preAggregations: 0` there is expected, because
  pre-aggregations live on the private cubes. To prove a rollup is used, read the SQL from
  `/v1/sql` and look for the rollup table name.
- **`cube_client.member_names()` returns QUALIFIED names** (`sales.win_rate`). Comparing a bare
  name against a qualified one matches nothing and turns an assertion into a vacuous pass — use
  `short_names()` whenever comparing against literal member names.
- **Segment names share the member namespace** with measures and dimensions in the same cube. Name
  a segment for the **row set**, not the column: `approved_deals`, not `approved`.
- **Dev mode ignores access policies.** Never run the access tier with `CUBEJS_DEV_MODE=true` —
  every assertion would pass vacuously.
- **Multi-stage `add_group_by` does not widen a denominator.** For a share-of-total, pin the
  denominator with `group_by: [<dimension>]`; otherwise it follows the query's grain and the share
  comes out as 100%.
- **A pre-aggregation only routes if the refresh worker built it.** Seeing the rollup name in the
  SQL from `/v1/sql` proves *routing*, not materialisation — with no data behind it, nothing fails.
  On real data a matching query errors with "no pre-aggregation partitions were built yet" unless
  `CUBEJS_REFRESH_WORKER=true` is set.
- **`.sh` files must stay LF.** `.gitattributes` pins it, but a ZIP download or a stray
  `core.autocrlf` still yields CRLF, and the container then dies with `env: 'bash
'` — a message
  that names neither the file nor the cause. The Dockerfile strips CR defensively.
- **`@config` decorator names change between versions.** Several were renamed in 1.7
  (`context_to_roles` → `context_to_groups`, and `access_policy`'s `role:` → `group:`) with no
  backward compatibility. Validate against the installed version.
- **Never version secrets:** `.env`, `token.txt` and any credential file are in `.gitignore`. Generated output (`warehouse/seed/data/`, `*.duckdb`) is ignored too — it is reproducible.

## Before calling a model change done

Parsing the YAML is **not** validation — the failure modes above only surface at boot.

```bash
docker compose up -d --build            # seed builds the warehouse, then the model must compile
curl -s -o /dev/null -w "%{http_code}" http://localhost:4000/cubejs-api/v1/meta   # expect 200
pytest                                  # expect all green
ruff check .                            # PEP 8 + imports + typing + docstrings
python scripts/lint_model.py            # Jinja renders, no duplicated YAML keys
```

After changing a cube's **columns**, regenerate the schema and rebuild the warehouse, or the model
will reference a column the tables do not have:

```bash
python warehouse/seed/load_duckdb.py --force
```

(`tests/conftest.py` reads the repo's `.env`, so the personas are signed with the same secret the
running container uses — no exports needed.)

If you touched **access** (`rbac.py`, `cube.py`, a view's `access_policy`), the contract tier is the
proof — it asserts both axes and the row filter, per persona. If you touched a **measure's shape**
(multi-stage, rolling, pre-aggregation), also read the generated SQL from `/v1/sql`: it is the only
way to see which grain the engine actually used, and the only way to confirm a rollup was hit.
