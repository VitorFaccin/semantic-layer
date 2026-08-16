# Reuse patterns — `extends`, Jinja macros, and `globals.py`

Cube renders every `.yml` as a **Jinja template** before compiling the YAML. That is what lets a
model inject Python functions (`model/globals.py`) and macros (`model/macros.jinja`) into its
definitions.

There are three reuse tools. They are not interchangeable, and picking the wrong one is how a model
ends up with three slightly different definitions of the same metric.

| Tool | Lives in | Reuses | Relationship it creates |
|---|---|---|---|
| **`extends`** | a `.yml` cube | dimensions / measures / segments | a **real** parent–child link Cube understands |
| **Jinja macro** | `model/macros.jinja` | a **block of YAML** | none — it copies text at render time |
| **Jinja function** | `model/globals.py` | a **computed value** (usually a SQL fragment) | none — it returns a string |

**Order of preference: `extends` → macro → global function.** Reach for the most structural one
that fits, because only `extends` keeps the definitions genuinely singular.

---

## 1. `extends` — inheriting real member definitions

Use it when several cubes share the **same members with the same meaning**. This repo's three
product verticals are exactly that case: an appliance deal and a furniture deal move through the
same pipeline, so they should answer "quantos negócios ganhos?" identically.

`model/cubes/_base/base_deal_pipeline.yml` is abstract — never queried directly:

```yaml
{% import 'macros.jinja' as m %}
cubes:
  - name: base_deal_pipeline
    sql_table: {{ table('mart_pipeline', 'deal_pipeline_template') }}
    public: false
    dimensions:
      - name: status
        sql: status
        type: string
        description: "Estado atual do negócio na esteira…"
    measures:
{{ m.deal_stage_measures() }}     # won / lost / open + win_rate
```

Each vertical inherits it and declares only what is genuinely its own:

```yaml
# model/cubes/furniture/base_operational_furniture.yml
cubes:
  - name: base_operational_furniture
    extends: base_deal_pipeline
    sql_table: {{ table('mart_pipeline', 'deal_pipeline_furniture') }}
    public: false
    dimensions:
      - name: lead_time_days      # furniture-only: made-to-order lead time
        sql: lead_time_days
        type: number
```

**Why this beats a macro here:** change a description in the abstract cube and all three verticals
update, because there is one definition. A macro would have copied the text three times, and the
copies drift. `tests/contract/test_cube_features.py` pins both halves of the invariant — the
inherited core must stay identical across verticals, and each vertical's extras must stay declared.

> The extending cube **overrides** `sql` / `sql_table`. Every inherited member must reference a
> column that exists in every table that extends the base.

---

## 2. Jinja macros — repeating a block of YAML

Use it when what repeats is **a piece of the model itself** and there is no parent–child
relationship to express: the same shape, in cubes that are otherwise unrelated.

```jinja
{# model/macros.jinja #}
{% macro ratio_measure(name, num, den, description='', fmt='percent', title='') %}
{% set _expr = '100.0 * (' ~ num ~ ') / NULLIF(' ~ den ~ ', 0)' %}
      - name: {{ name }}
        type: number
        sql: {{ _expr }}
        format: {{ fmt }}
{% if title %}        title: {{ title }}
{% endif %}        description: {{ description }}
        meta:
          additive: false
{% endmacro %}
```

Applied by importing at the top of the `.yml` and expanding inside `measures:`:

```yaml
{% import 'macros.jinja' as m %}
cubes:
  - name: base_funnel
    measures:
{{ m.funnel_stage_measures() }}
```

The macros in this repo, and why each exists:

| Macro | What it guarantees |
|---|---|
| `access_policy_transversal(min_level)` | every transversal view is gated the same way |
| `rep_attribute_block(join_path, extra)` | every fact view offers the **same** rep cuts — without it, one view offers `territory` and another does not, silently killing a whole question class |
| `commission_measures()` | the total/company/rep trio, only ever on a sale-grain cube |
| `ratio_measure(...)` / `filtered_count(...)` | ratios are always `NULLIF`-guarded and always flagged non-additive |
| `deal_stage_measures()` / `funnel_stage_measures()` | the stage vocabulary stays identical across pipelines |

### Two traps, both learned the hard way

**Indentation.** The macro emits raw text that is concatenated into the `.yml` *before* parsing, so
it must emit correctly-indented YAML (2 spaces). A misindented macro fails as a YAML error in a file
that looks fine.

**Cube JSON-quotes every interpolated value.** Writing `title: "{{ title }}"` renders as
`title: ""Negócios ganhos""` and the YAML dies. Omit the outer quotes, and build composite SQL with
`{% set %}` before emitting it — that is why `ratio_measure` computes `_expr` on its own line.

---

## 3. `globals.py` — a business rule computed in Python

Use it when the repeated thing is a **value**, typically a SQL fragment, and expressing it needs
actual logic rather than a text block.

```python
# model/globals.py
from cube import TemplateContext

template = TemplateContext()
WAREHOUSE = "`meridian-analytics`"


@template.function
def table(dataset: str, name: str) -> str:
    """Fully-qualified table reference — a fork repoints the whole model by editing WAREHOUSE."""
    return f"{WAREHOUSE}.{dataset}.{name}"


@template.function
def canonical_order_filter(t: str) -> str:
    """The CANONICAL order-counting rule, in one place: root orders only, excluding self-purchases."""
    return f"{t}.parent_order_id IS NULL AND {t}.customer_id != {t}.rep_id"
```

Used in a cube by switching `sql_table:` for a `sql:` with a SELECT:

```yaml
cubes:
  - name: base_orders
    sql: >
      SELECT * FROM {{ table('core_orders', 'orders') }} AS t
      WHERE {{ valid_rows('t', 'created_at') }}
    public: false
```

**Why `canonical_order_filter` is a function and not a comment:** "an order counts when it is a root
order and is not a self-purchase" is the kind of rule that gets re-implemented slightly differently
in each place it is needed, and then two views disagree about how many orders exist. One function,
one definition, and every measure that claims to count orders applies exactly it.

Values can be registered too:

```python
template.add_variable("product_lines", ["Appliances", "Furniture", "Electronics"])
```

> **Do not confuse `model/globals.py` with the root `cube.py`.** `globals.py` feeds the *model* at
> render time; `cube.py` is the *runtime* (auth, RLS, cache scoping). They never overlap.

---

## Choosing, in one question

> *Is the thing I am repeating a **definition**, a **shape**, or a **value**?*

- **Definition** (same member, same meaning, several cubes) → `extends`
- **Shape** (same YAML structure, different meanings or unrelated cubes) → macro
- **Value** (a SQL fragment or a computed constant) → `globals.py`

Everything renders through Jinja, so a mistake in any of the three surfaces as a **model
compilation failure**. Check the Jinja before suspecting the YAML — and remember that a compile
error is the *good* outcome. The bad one is a duplicated YAML key, which Python's parser accepts and
Cube rejects only at boot.
