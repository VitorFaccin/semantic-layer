"""Derive the warehouse schema from the Cube model itself.

Hand-writing the DDL would let it drift from what the cubes actually reference: a renamed column
breaks at query time, on a warehouse, which is the most expensive place to find it. Instead this
reads `model/cubes/**`, renders the Jinja exactly as Cube does (so macro-generated members and
`extends` are included), and emits `CREATE TABLE` for every table the model expects.

Run it whenever the model changes:
    python warehouse/build_schema.py

Outputs:
    warehouse/ddl/schema.sql   — CREATE SCHEMA + CREATE TABLE for every referenced table
    warehouse/schema.json      — the same, machine-readable, consumed by seed/generate.py
"""
from __future__ import annotations

import json
import pathlib
import re

import yaml
from jinja2 import Environment, FileSystemLoader

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODEL = ROOT / "model"
OUT_DDL = ROOT / "warehouse" / "ddl" / "schema.sql"
OUT_JSON = ROOT / "warehouse" / "schema.json"

# Identifiers that appear inside a `sql:` expression but are NOT columns.
SQL_NOISE = {
    "cast", "as", "timestamp", "date", "datetime", "numeric", "string", "bool", "int64", "float64",
    "timestamp_diff", "date_diff", "datetime_diff", "day", "month", "year", "week", "quarter",
    "nullif", "coalesce", "sum", "count", "avg", "min", "max", "safe_divide", "round", "abs",
    "true", "false", "null", "is", "not", "in", "and", "or", "then", "case", "when", "else", "end",
    "distinct", "extract", "current_timestamp", "current_date", "interval", "cube",
}

# Cube member type -> warehouse column type.
TYPE_MAP = {"string": "STRING", "number": "NUMERIC", "time": "TIMESTAMP", "boolean": "BOOL"}


def render(path: pathlib.Path) -> str:
    """Render one model file through Jinja, the way Cube does before parsing the YAML.

    Args:
        path (pathlib.Path): The .yml file to render.

    Returns:
        str: The rendered YAML text.
    """
    def quote_like_cube(value: object) -> object:
        """Mirror Cube's own rendering: an interpolated SCALAR comes out JSON-quoted.

        Without this, a macro emitting `sql: {CUBE}.status = 'won'` produces YAML that PyYAML
        rejects (the brace opens a flow mapping) — while Cube parses it fine, because it quoted the
        value first. Multi-line values are macro-expanded YAML blocks and must pass through raw.
        """
        if isinstance(value, str) and "\n" not in value:
            return json.dumps(value)
        return value

    env = Environment(loader=FileSystemLoader(str(MODEL)), keep_trailing_newline=True,
                      finalize=quote_like_cube)
    env.globals["table"] = lambda dataset, name: f"{dataset}.{name}"
    env.globals["valid_rows"] = lambda t, date_col="created_at": f"{t}.{date_col} IS NOT NULL"
    env.globals["canonical_order_filter"] = (
        lambda t: f"{t}.parent_order_id IS NULL AND {t}.customer_id != {t}.rep_id"
    )
    rel = path.relative_to(MODEL).as_posix()
    return env.get_template(rel).render()


def columns_in(expr: str) -> set[str]:
    """Extract the bare column identifiers referenced by a `sql:` expression.

    Curly-brace tokens are Cube MEMBER references (`{total_sale_amount}`), not columns, so they are
    stripped first — except `{CUBE}.`, which prefixes a real column.

    Args:
        expr (str): The raw value of a `sql:` key.

    Returns:
        set[str]: The column names it touches.
    """
    if not isinstance(expr, str):
        return set()
    expr = expr.replace("{CUBE}.", " ")
    expr = re.sub(r"\{[^}]*\}", " ", expr)          # drop member references
    expr = re.sub(r"'[^']*'", " ", expr)            # drop string literals
    # Anything immediately followed by "(" is a function call, not a column. Detecting the call
    # shape beats keeping a denylist of SQL builtins, which silently misses the next one.
    expr = re.sub(r"[A-Za-z_][A-Za-z0-9_]*\s*\(", " (", expr)
    found = set()
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr):
        if token.lower() in SQL_NOISE:
            continue
        found.add(token)
    return found


def collect() -> dict:
    """Walk every cube and build {table: {column: type}}.

    Returns:
        dict: Table reference -> column name -> warehouse type.
    """
    raw: dict[str, dict] = {}
    for path in sorted(MODEL.glob("cubes/**/*.yml")):
        doc = yaml.safe_load(render(path)) or {}
        for cube in doc.get("cubes", []) or []:
            raw[cube["name"]] = cube

    schema: dict[str, dict[str, str]] = {}
    for name, cube in raw.items():
        table = cube.get("sql_table") or ""
        if not table:
            continue
        # `extends` copies the parent's members in; those columns must exist in the child's table.
        chain = [cube]
        parent = cube.get("extends")
        while parent and parent in raw:
            chain.append(raw[parent])
            parent = raw[parent].get("extends")

        cols: dict[str, str] = {}

        def note(col: str, sql_type: str, *, strong: bool) -> None:
            """Record a column, letting an explicitly typed member win over an inferred one."""
            if strong or col not in cols:
                cols[col] = sql_type

        for member_cube in chain:
            for dim in member_cube.get("dimensions", []) or []:
                sql_type = TYPE_MAP.get(dim.get("type", "string"), "STRING")
                found = columns_in(dim.get("sql", dim["name"]))
                # A single-column dimension carries its declared type; an expression does not.
                strong = len(found) == 1
                for c in found:
                    note(c, sql_type, strong=strong)
            for mea in member_cube.get("measures", []) or []:
                agg = (mea.get("type") or "").lower()
                for c in columns_in(mea.get("sql", "")):
                    numeric_agg = agg in {"sum", "avg", "min", "max", "number"}
                    inferred = "NUMERIC" if numeric_agg else "STRING"
                    note(c, inferred, strong=False)
                for f in mea.get("filters", []) or []:
                    for c in columns_in(f.get("sql", "")):
                        note(c, "STRING", strong=False)
            for seg in member_cube.get("segments", []) or []:
                expr = seg.get("sql", "")
                for c in columns_in(expr):
                    inferred = "BOOL" if re.search(rf"{c}\s*=\s*(TRUE|FALSE)", expr) else "STRING"
                    note(c, inferred, strong=False)
            for join in member_cube.get("joins", []) or []:
                for c in columns_in(join.get("sql", "")):
                    note(c, "STRING", strong=False)

        schema.setdefault(table, {}).update(cols)
    return schema


def check_join_key_types(schema: dict) -> list[str]:
    """Report columns whose inferred type differs between tables.

    A join key typed STRING on one side and NUMERIC on the other compiles fine and then fails — or
    worse, silently matches nothing — at query time. Catching it here keeps the model internally
    consistent before anything is loaded.

    Args:
        schema (dict): Table -> column -> type.

    Returns:
        list[str]: One message per inconsistent column (empty when all agree).
    """
    seen: dict[str, dict[str, list[str]]] = {}
    for table, cols in schema.items():
        for col, sql_type in cols.items():
            seen.setdefault(col, {}).setdefault(sql_type, []).append(table)
    problems = []
    for col, types in sorted(seen.items()):
        if len(types) > 1:
            detail = "; ".join(f"{t} em {', '.join(tbls)}" for t, tbls in types.items())
            problems.append(f"{col}: {detail}")
    return problems


def main() -> None:
    """Write the DDL and its machine-readable twin."""
    schema = collect()
    problems = check_join_key_types(schema)
    if problems:
        print("TIPOS DIVERGENTES entre tabelas (corrija o modelo):")
        for p in problems:
            print("  -", p)
    OUT_DDL.parent.mkdir(parents=True, exist_ok=True)

    datasets = sorted({t.split(".")[0] for t in schema})
    lines = [
        "-- GENERATED by warehouse/build_schema.py — do not edit by hand.",
        "-- Every table and column below is referenced by a cube in model/cubes/.",
        "-- Targets DuckDB (the default local warehouse); see warehouse/README.md to retarget.",
        "",
    ]
    for ds in datasets:
        lines.append(f"CREATE SCHEMA IF NOT EXISTS {ds};")
    lines.append("")

    for table in sorted(schema):
        cols = schema[table]
        lines.append(f"CREATE TABLE IF NOT EXISTS {table} (")
        width = max(len(c) for c in cols)
        body = [f"  {c.ljust(width)}  {t}" for c, t in sorted(cols.items())]
        lines.append(",\n".join(body))
        lines.append(");")
        lines.append("")

    OUT_DDL.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")
    print(f"{len(schema)} tabelas, {sum(len(c) for c in schema.values())} colunas")
    print(f"  {OUT_DDL.relative_to(ROOT)}")
    print(f"  {OUT_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
