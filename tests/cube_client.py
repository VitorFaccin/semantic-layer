"""Tiny client + meta helpers for testing a running Cube instance.

Tests hit the Cube REST API (default http://localhost:4000). In dev mode no token is required;
otherwise set CUBE_API_TOKEN (or CUBEJS_API_SECRET) in the environment. The `*_as` helpers mint a
signed JWT so a query can run AS a specific security context (used by the access-policy tests).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable

import requests

CUBE_URL = os.environ.get("CUBE_URL", "http://localhost:4000")
_API = f"{CUBE_URL}/cubejs-api/v1"
_TOKEN = os.environ.get("CUBE_API_TOKEN") or os.environ.get("CUBEJS_API_SECRET") or ""
_SECRET = os.environ.get("CUBEJS_API_SECRET") or _TOKEN


def _headers() -> dict:
    """Build the default request headers (raw token from the environment, if any).

    Returns:
        dict: The headers dict, with Authorization set when a token is configured.
    """
    h = {"Content-Type": "application/json"}
    if _TOKEN:
        h["Authorization"] = _TOKEN
    return h


def _b64url(data: bytes) -> str:
    """Base64url-encode bytes without padding (JWT segment encoding).

    Args:
        data (bytes): the raw bytes to encode.

    Returns:
        str: The padding-stripped base64url string.
    """
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint_jwt(claims: dict, secret: str | None = None) -> str:
    """Mint an HS256 JWT (stdlib, no PyJWT) carrying the access claims.

    Args:
        claims (dict): the JWT payload (level/domains/is_admin/sub).
        secret (str | None): signing secret; defaults to CUBEJS_API_SECRET.

    Returns:
        str: The encoded JWT string.
    """
    secret = secret if secret is not None else _SECRET
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(sig)}"


def _headers_as(claims: dict) -> dict:
    """Build request headers authenticated as a given security context.

    Args:
        claims (dict): the security-context claims to mint into the JWT.

    Returns:
        dict: The headers dict with the minted Authorization JWT.
    """
    return {"Content-Type": "application/json", "Authorization": mint_jwt(claims)}


def get_meta_as(claims: dict) -> dict:
    """Fetch /meta AS the given security context (minted JWT).

    Args:
        claims (dict): the security-context claims to authenticate as.

    Returns:
        dict: The parsed /meta response for that context.
    """
    r = requests.get(f"{_API}/meta", headers=_headers_as(claims), timeout=60)
    r.raise_for_status()
    return r.json()


def load_as(claims: dict, query: dict, timeout: int = 180) -> dict:
    """Run a query AS the given security context (minted JWT), retrying 'Continue wait'.

    Args:
        claims (dict): the security-context claims to authenticate as.
        query (dict): the Cube query.
        timeout (int): seconds to keep retrying Cube's async "Continue wait".

    Returns:
        dict: The parsed /load response.

    Raises:
        TimeoutError: if "Continue wait" persists past the timeout.
        AssertionError: on a non-200 response.
    """
    deadline = time.time() + timeout
    params = {"query": json.dumps(query)}
    while True:
        r = requests.get(f"{_API}/load", params=params, headers=_headers_as(claims), timeout=90)
        if r.status_code == 200:
            body = r.json()
            if isinstance(body, dict) and str(body.get("error", "")).lower() == "continue wait":
                if time.time() > deadline:
                    raise TimeoutError("Cube 'Continue wait' exceeded timeout")
                time.sleep(2)
                continue
            return body
        raise AssertionError(f"/load HTTP {r.status_code}: {r.text[:500]}")


def get_meta() -> dict:
    """Fetch /meta with the default token.

    Returns:
        dict: The parsed /meta response.
    """
    r = requests.get(f"{_API}/meta", headers=_headers(), timeout=60)
    r.raise_for_status()
    return r.json()


def load(query: dict, timeout: int = 180) -> dict:
    """Run a Cube query with the default token, retrying the async 'Continue wait' response.

    Args:
        query (dict): the Cube query.
        timeout (int): seconds to keep retrying Cube's async "Continue wait".

    Returns:
        dict: The parsed /load response.

    Raises:
        TimeoutError: if "Continue wait" persists past the timeout.
        AssertionError: on a non-200 response.
    """
    deadline = time.time() + timeout
    params = {"query": json.dumps(query)}
    while True:
        r = requests.get(f"{_API}/load", params=params, headers=_headers(), timeout=90)
        if r.status_code == 200:
            body = r.json()
            if isinstance(body, dict) and str(body.get("error", "")).lower() == "continue wait":
                if time.time() > deadline:
                    raise TimeoutError("Cube 'Continue wait' exceeded timeout")
                time.sleep(2)
                continue
            return body
        raise AssertionError(f"/load HTTP {r.status_code}: {r.text[:500]}")


def measure_value(query: dict) -> dict:
    """Run a single-measure/-dimension query and return the first row.

    Args:
        query (dict): the Cube query.

    Returns:
        dict: The first row dict, or {} if there are no rows.
    """
    rows = load(query).get("data", [])
    return rows[0] if rows else {}


def all_cubes(meta: dict) -> list:
    """Return the private cubes from a /meta response.

    Args:
        meta (dict): a /meta response.

    Returns:
        list: The list of members whose type is 'cube'.
    """
    return [c for c in meta["cubes"] if c.get("type") == "cube"]


def all_views(meta: dict) -> list:
    """Return the public views from a /meta response.

    Args:
        meta (dict): a /meta response.

    Returns:
        list: The list of members whose type is 'view'.
    """
    return [c for c in meta["cubes"] if c.get("type") == "view"]


def get(meta: dict, name: str) -> dict | None:
    """Find a cube/view by name in a /meta response.

    Args:
        meta (dict): a /meta response.
        name (str): the cube/view name.

    Returns:
        dict | None: The matching entry, or None if absent.
    """
    return next((c for c in meta["cubes"] if c["name"] == name), None)


def measures(meta: dict, name: str) -> list:
    """Return the measures of a cube/view.

    Args:
        meta (dict): a /meta response.
        name (str): the cube/view name.

    Returns:
        list: The measures list (empty if the cube/view is absent).
    """
    c = get(meta, name)
    return c["measures"] if c else []


def dimensions(meta: dict, name: str) -> list:
    """Return the dimensions of a cube/view.

    Args:
        meta (dict): a /meta response.
        name (str): the cube/view name.

    Returns:
        list: The dimensions list (empty if the cube/view is absent).
    """
    c = get(meta, name)
    return c["dimensions"] if c else []


def segments(meta: dict, name: str) -> list:
    """Return the segments of a cube/view.

    Args:
        meta (dict): a /meta response.
        name (str): the cube/view name.

    Returns:
        list: The segments list (empty if the cube/view is absent or declares none).
    """
    c = get(meta, name)
    return (c.get("segments") or []) if c else []


def member_names(members: list) -> list:
    """Extract the qualified names from a list of members.

    Args:
        members (list): measure/dimension dicts.

    Returns:
        list: The list of their 'name' values.
    """
    return [m["name"] for m in members]


def short_names(members: list) -> list:
    """Extract the members' names WITHOUT the `<view>.` prefix.

    /meta qualifies every member (`sales.win_rate`). Comparing a bare name against a qualified one
    silently matches nothing, which turns a "does this view leak PII?" assertion into a test that
    passes vacuously — so anything comparing against literal member names must use this.

    Args:
        members (list): measure/dimension/segment dicts.

    Returns:
        list: The list of their unqualified names.
    """
    return [m["name"].split(".", 1)[-1] for m in members]


def measure_errors(meta: dict, name: str, load_fn: Callable) -> list:
    """Smoke a view's measures with ONE batched query; on failure, retry per measure.

    Batch-first keeps the smoke tier at ~1 warehouse query PER VIEW instead of one per
    measure (~37 vs ~250 queries across the suite) — fast enough sequentially, with no
    parallelism and therefore no memory pressure on the CI container. The per-measure
    fallback runs only for a view whose batch fails, so a failure is still attributed
    to the exact measure (worst case for that one view = the old per-measure cost).

    Args:
        meta (dict): a /meta response.
        name (str): the view name.
        load_fn (Callable): the query runner (the `load` fixture).

    Returns:
        list: A list of "measure: error" strings for the measures that individually fail
        (empty when the view is healthy).
    """
    names = member_names(measures(meta, name))
    if not names:
        return []
    try:
        if "data" in load_fn({"measures": names, "limit": 1}):
            return []
    except Exception:  # noqa: BLE001 — batch may fail for mixing reasons; attribute below
        pass
    errors = []
    for m in names:
        try:
            res = load_fn({"measures": [m], "limit": 1})
            assert "data" in res, f"{m}: no data key in response"
        except Exception as exc:  # noqa: BLE001 — aggregate all failures
            errors.append(f"{m}: {exc}")
    return errors


def _reference_count(meta: dict, name: str) -> str | None:
    """Pick a COUNT-like measure of a view, to validate segments against.

    Selection is by `aggType` from /meta, not by name: counting measures are not consistently
    suffixed `_count` in this model (`funnel` counts are `funnel_quotes`, `funnel_orders`, …),
    and a name heuristic silently skipped that whole view.

    Args:
        meta (dict): a /meta response.
        name (str): the view name.

    Returns:
        str | None: Qualified name of a count/count_distinct measure, or None when absent.
    """
    for m in measures(meta, name):
        if (m.get("aggType") or "").lower() in ("count", "countdistinct"):
            return m["name"]
    return next((m["name"] for m in measures(meta, name) if m["name"].endswith("_count")), None)


def segment_errors(meta: dict, name: str, load_fn: Callable) -> list:
    """Smoke a view's segments: each one must be APPLICABLE and must not fan out.

    Same batch-first shape as `measure_errors` (one query per view, per-segment fallback only on
    failure) to keep the smoke tier cheap — see that docstring for why.

    Two invariants per segment, both checked against the same reference measure:
      1. it compiles and returns data;
      2. the segmented count is NEVER GREATER than the unsegmented one. A segment is a WHERE
         clause, so it can only reduce or keep the row set — an increase means the join it travels
         through fans out.

    Args:
        meta (dict): a /meta response.
        name (str): the view name.
        load_fn (Callable): the query runner (the `load` fixture).

    Returns:
        list: A list of "segment: problem" strings (empty when the view is healthy).
    """
    seg_names = member_names(segments(meta, name))
    if not seg_names:
        return []
    ref = _reference_count(meta, name)
    if ref is None:
        return [f"{name}: no count-like measure to validate segments against"]

    baseline_rows = load_fn({"measures": [ref], "limit": 1}).get("data", [])
    baseline = float(baseline_rows[0][ref] or 0) if baseline_rows else 0.0

    errors = []
    for s in seg_names:
        try:
            rows = load_fn({"measures": [ref], "segments": [s], "limit": 1}).get("data", [])
            value = float(rows[0][ref] or 0) if rows else 0.0
        except Exception as exc:  # noqa: BLE001 — aggregate all failures
            errors.append(f"{s}: {exc}")
            continue
        if value > baseline + 0.5:
            errors.append(
                f"{s}: FAN-OUT — {ref} grew from {baseline} to {value} with the segment on; "
                "a segment can only filter, so the join it travels through is multiplying rows")
    return errors


def view_quality_issues(meta: dict, name: str, require_descriptions: bool = True) -> list:
    """Run structural contract checks for a PUBLIC view — the surface the agent actually reads.

    Runs with dev mode OFF (as in prod), where the REST /meta exposes only views; the private base_*
    cubes are intentionally hidden, so we assert the storefront, not the private internals.

    Args:
        meta (dict): a /meta response.
        name (str): the view name to check.
        require_descriptions (bool): also require every member to carry a non-empty description.

    Returns:
        list: A list of problem strings (empty when the view is well-formed).
    """
    v = get(meta, name)
    if v is None:
        return [f"view '{name}' not found in meta"]
    issues = []
    if not v["measures"] and not v["dimensions"]:
        issues.append(f"{name} exposes no members")
    if require_descriptions:
        for m in v["measures"]:
            if not (m.get("description") or "").strip():
                issues.append(f"{name}.{m['name']} (measure) has no description")
        for d in v["dimensions"]:
            if not (d.get("description") or "").strip():
                issues.append(f"{name}.{d['name']} (dimension) has no description")
    return issues
