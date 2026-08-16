"""Fixtures for the MCP gateway tests.

`live_client` is a session fixture that wires a real Cube client into the tools and SKIPS
if Cube isn't reachable — integration tests request it explicitly. Pure unit tests (query
building / normalization) do NOT request it, so they run even without Cube up.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import httpx
import pytest
from app import server as srv
from app.config import settings
from app.cube_client import CubeClient

CUBE_URL = os.getenv("CUBE_URL", settings.cube_url)


def _cube_up() -> bool:
    """Check whether Cube is reachable at CUBE_URL.

    Returns:
        bool: True if /meta answers 200, else False.
    """
    try:
        r = httpx.get(f"{CUBE_URL.rstrip('/')}/cubejs-api/v1/meta", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="session")
def live_client() -> Iterator[CubeClient]:
    """Wire a real Cube client into the tools, skipping if Cube is not reachable.

    Yields:
        CubeClient: The wired client (closed on teardown).
    """
    if not _cube_up():
        pytest.skip(f"Cube not reachable at {CUBE_URL} — start it with `docker compose up`.")
    client = CubeClient(CUBE_URL, settings.cube_api_secret, settings.query_timeout)
    srv.set_client(client)
    yield client
    asyncio.get_event_loop().run_until_complete(client.aclose())


@pytest.fixture(scope="session")
def warehouse_client(live_client: CubeClient) -> Iterator[CubeClient]:
    """Like `live_client`, but also requires the warehouse to actually answer a query.

    Metadata tools (`list_metrics`, `describe_view`) only need Cube compiled; `run_query` needs a
    real warehouse behind it. This repo ships a fictional warehouse, so those tests skip rather
    than fail — same tiering as the main suite's `warehouse` marker.

    Args:
        live_client (CubeClient): The already-wired client from the fixture above.

    Yields:
        CubeClient: The same client, once a real query has been proven to work.
    """
    try:
        asyncio.get_event_loop().run_until_complete(
            live_client.load({"measures": ["sales.sales_count"], "limit": 1})
        )
    except Exception as exc:  # no warehouse behind this Cube
        pytest.skip(f"warehouse not reachable through Cube ({type(exc).__name__}) — "
                    "point CUBEJS_DB_* at a real warehouse to run this tier.")
    yield live_client
