"""Build the local DuckDB warehouse from the generated schema and seed.

This is what makes `docker compose up` enough: no cloud project, no credentials, no manual load.
It runs the two generators when their output is missing, then materialises a single `.duckdb`
file the Cube container mounts read-only.

The file is NOT versioned. It is bulk output of a deterministic generator, and a binary in git
costs a fresh multi-megabyte blob on every regeneration — permanently, since history never shrinks.

Run:
    python warehouse/seed/load_duckdb.py            # skips work that is already done
    python warehouse/seed/load_duckdb.py --force    # rebuild from scratch
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import duckdb

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SCHEMA_JSON = ROOT / "warehouse" / "schema.json"
DDL = ROOT / "warehouse" / "ddl" / "schema.sql"
DATA = ROOT / "warehouse" / "seed" / "data"
DB = ROOT / "warehouse" / "meridian.duckdb"


def run(script: pathlib.Path) -> None:
    """Run one of the generator scripts in a subprocess.

    Args:
        script (pathlib.Path): The generator to execute.
    """
    print(f"-> {script.relative_to(ROOT)}")
    subprocess.run([sys.executable, str(script)], check=True, cwd=ROOT)


def build(force: bool = False) -> pathlib.Path:
    """Create the DuckDB file, generating the schema and seed first if needed.

    Args:
        force (bool): Rebuild even when the database already exists.

    Returns:
        pathlib.Path: Path to the database file.
    """
    if DB.exists() and not force:
        print(f"{DB.relative_to(ROOT)} já existe — nada a fazer (use --force para refazer)")
        return DB
    if force or not SCHEMA_JSON.exists():
        run(ROOT / "warehouse" / "build_schema.py")
    if force or not any(DATA.glob("*.csv")):
        run(ROOT / "warehouse" / "seed" / "generate.py")

    # Drop the write-ahead log alongside the database. A .wal left over from a PREVIOUS database
    # is replayed into the new one on open — silent corruption that only shows up as wrong numbers.
    DB.unlink(missing_ok=True)
    DB.with_suffix(".duckdb.wal").unlink(missing_ok=True)
    con = duckdb.connect(str(DB))
    con.execute(DDL.read_text(encoding="utf-8"))

    schema = json.loads(SCHEMA_JSON.read_text(encoding="utf-8"))
    for table in sorted(schema):
        csv = DATA / f"{table.replace('.', '__')}.csv"
        if not csv.exists():
            print(f"   ! sem CSV para {table}")
            continue
        # The DDL already fixed the column types; read_csv is told to honour them rather than
        # re-sniffing, so an all-empty column stays TIMESTAMP instead of becoming VARCHAR.
        cols = ", ".join(f"'{c}': '{t}'" for c, t in sorted(schema[table].items()))
        con.execute(
            f"INSERT INTO {table} SELECT * FROM "
            f"read_csv('{csv.as_posix()}', header=true, columns={{{cols}}}, nullstr='')"
        )
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in sorted(schema)}
    # Fold the WAL into the database file, so the size reported below is the settled one and no
    # .wal is left behind for the next process to replay.
    con.execute("CHECKPOINT")
    con.close()

    total = sum(counts.values())
    print(f"\n{len(counts)} tabelas, {total:,} linhas -> {DB.relative_to(ROOT)} "
          f"({DB.stat().st_size / 1e6:.1f} MB)")
    return DB


if __name__ == "__main__":
    build(force="--force" in sys.argv)
