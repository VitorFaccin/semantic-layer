"""Async client for the Cube Core REST API.

Mirrors the proven contract from the repo's tests/cube_client.py:
- Authorization header carries the token directly (no "Bearer" prefix);
- /load may answer {"error": "Continue wait"} while materializing — retry;
- query shape is Cube's: measures/dimensions/timeDimensions[].granularity/filters[].
Adds /sql (compiled SQL) and /dry-run (validate without executing) for the gateway.

Auth: Cube (non-dev mode) verifies a JWT signed with CUBEJS_API_SECRET. Each request mints a fresh
JWT whose payload is the security context forwarded from the agent's validated token (see app.auth),
so check_auth/access_policy/query_rewrite enforce the caller's level/domains.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import jwt

from . import auth


class CubeClient:
    """Thin async HTTP client for one Cube Core instance's REST API."""

    def __init__(self, base_url: str, api_secret: str, query_timeout: int = 180) -> None:
        """Initialize the client.

        Args:
            base_url (str): Cube base URL (the /cubejs-api/v1 suffix is appended internally).
            api_secret (str): shared secret used to sign the Cube JWT (CUBEJS_API_SECRET).
            query_timeout (int): seconds retrying Cube's async "Continue wait" before giving up.
        """
        self._api = f"{base_url.rstrip('/')}/cubejs-api/v1"
        self._secret = api_secret
        self._query_timeout = query_timeout
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(90.0))

    def _headers(self) -> dict:
        """Build request headers, minting the Cube JWT for the current security context.

        The JWT payload is the context forwarded from the agent's validated token
        (level/domains/is_admin/user_id); an empty payload (no HTTP context / no token) lets Cube
        apply its own fallback.

        Returns:
            dict: The headers dict (Content-Type, and Authorization when a secret is configured).
        """
        h = {"Content-Type": "application/json"}
        if self._secret:
            claims = auth.current_security_context()
            h["Authorization"] = jwt.encode(claims, self._secret, algorithm="HS256")
        return h

    async def meta(self) -> dict:
        """Fetch the data model metadata (/meta).

        Returns:
            dict: The parsed /meta response (cubes/views and their members).
        """
        r = await self._http.get(f"{self._api}/meta", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def load(self, query: dict) -> dict:
        """Execute a query, transparently retrying Cube's async 'Continue wait'.

        Args:
            query (dict): a Cube query (measures/dimensions/timeDimensions/filters/...).

        Returns:
            dict: The parsed /load response.

        Raises:
            TimeoutError: if Cube keeps answering "Continue wait" past query_timeout.
            RuntimeError: on a non-200 response.
        """
        deadline = time.monotonic() + self._query_timeout
        while True:
            r = await self._http.post(f"{self._api}/load", headers=self._headers(),
                                      json={"query": query})
            if r.status_code == 200:
                body = r.json()
                if isinstance(body, dict) and str(body.get("error", "")).lower() == "continue wait":
                    if time.monotonic() > deadline:
                        raise TimeoutError("Cube 'Continue wait' exceeded timeout")
                    await asyncio.sleep(2)
                    continue
                return body
            raise RuntimeError(f"/load HTTP {r.status_code}: {r.text[:500]}")

    async def sql(self, query: dict) -> str | None:
        """Return the compiled SQL Cube would run for a query (transparency for the agent).

        Args:
            query (dict): a Cube query.

        Returns:
            str | None: The SQL text, or None if Cube did not return it.
        """
        r = await self._http.post(f"{self._api}/sql", headers=self._headers(),
                                  json={"query": query})
        if r.status_code != 200:
            return None
        block = (r.json() or {}).get("sql", {})
        sql = block.get("sql") if isinstance(block, dict) else None
        # Cube returns ["SELECT ... ?", [params]]; surface the text.
        if isinstance(sql, list) and sql:
            return sql[0]
        return sql if isinstance(sql, str) else None

    async def dry_run(self, query: dict) -> tuple[bool, str | None]:
        """Validate a query without executing it.

        Args:
            query (dict): a Cube query.

        Returns:
            tuple[bool, str | None]: (valid, error); error is None when valid, else truncated.
        """
        r = await self._http.post(f"{self._api}/dry-run", headers=self._headers(),
                                  json={"query": query})
        if r.status_code == 200:
            return True, None
        try:
            err = (r.json() or {}).get("error") or r.text
        except Exception:
            err = r.text
        return False, (err or "")[:500]

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()
