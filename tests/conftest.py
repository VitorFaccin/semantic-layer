"""Shared pytest fixtures for the test suite (a running Cube, plus an optional warehouse)."""
from __future__ import annotations

import os
import pathlib
import shutil
import tempfile
from collections.abc import Callable

import pytest


def _load_dotenv() -> None:
    """Load the repo's .env into the environment for any key not already set.

    The access tests mint one JWT per persona, which needs CUBEJS_API_SECRET — the same secret the
    running Cube was started with. Without this, `pytest -m contract` fails a dozen access tests
    with signature errors that look like a broken model rather than a missing variable. Reading the
    .env the container already uses makes the documented command work verbatim.

    Real environment variables win, so CI can override without touching the file.
    """
    env_file = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        # strip inline comments and surrounding quotes
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()  # must run BEFORE cube_client, which reads the secret at import time

import cube_client  # noqa: E402


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Selective smoke (PR tier): skip smoke tests for views untouched by the PR diff.

    SMOKE_ONLY_VIEWS (set by the pipeline from tests/affected_views.py) is one of:
      unset/empty/'ALL' -> run everything (local runs and main stay full);
      'NONE'            -> docs-only diff, skip every smoke;
      'v1,v2,...'       -> run only these views' smoke tests.
    Only `-m smoke` tests parametrized by `view` are filtered — contract/fanout/warehouse
    tiers always run in full.

    Args:
        item (pytest.Item): The test item pytest is about to run.
    """
    only = (os.environ.get("SMOKE_ONLY_VIEWS") or "").strip()
    if not only or only == "ALL" or "smoke" not in item.keywords:
        return
    view = getattr(item, "callspec", None) and item.callspec.params.get("view")
    if not view:
        return
    if only == "NONE" or view not in {v.strip() for v in only.split(",")}:
        pytest.skip(f"smoke skipped: view '{view}' not affected by this diff (SMOKE_ONLY_VIEWS)")

try:
    import duckdb
except ImportError:  # warehouse tier only — absent installs simply skip those tests
    duckdb = None


@pytest.fixture(scope="session")
def meta() -> dict:
    """Provide the compiled Cube metadata (also proves the model compiles).

    Returns:
        dict: The /meta response.
    """
    return cube_client.get_meta()


@pytest.fixture(scope="session")
def load() -> Callable:
    """Provide the query runner.

    Returns:
        Callable: cube_client.load — load(query) -> response dict (handles 'Continue wait').
    """
    return cube_client.load


@pytest.fixture(scope="session")
def warehouse_sql() -> Callable:
    """Provide a runner that queries the warehouse DIRECTLY, bypassing Cube.

    This is what makes a parity test meaningful: the same number computed two ways, once through
    the semantic layer and once straight from the source.

    It reads a COPY of the database. DuckDB takes an exclusive lock, and Cube holds the file open
    for the whole session — so opening the original here fails with "already in use", even
    read-only. The data does not change during a run, so a snapshot is equivalent and costs a
    ~8 MB copy.

    Skips when the warehouse has not been built (`python warehouse/seed/load_duckdb.py`).

    Returns:
        Callable: warehouse_sql(sql) -> list[dict].
    """
    if duckdb is None:
        pytest.skip("duckdb not installed (warehouse tier)")
    db = pathlib.Path(__file__).resolve().parent.parent / "warehouse" / "meridian.duckdb"
    if not db.exists():
        pytest.skip(f"{db.name} not built — run python warehouse/seed/load_duckdb.py")

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = pathlib.Path(tmp) / db.name
        shutil.copy2(db, snapshot)
        con = duckdb.connect(str(snapshot), read_only=True)

        def _query(sql: str) -> list:
            """Run a SQL statement against the warehouse snapshot.

            Args:
                sql (str): The statement to execute.

            Returns:
                list: One dict per result row.
            """
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

        yield _query
        con.close()
