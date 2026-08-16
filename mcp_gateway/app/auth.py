"""Agent-token auth for the MCP gateway (the role/domain transport).

Flow: the agent presents a SIGNED JWT (claims: level, domains, is_admin, sub/user_id) on the /mcp
request. The gateway validates it with AGENT_JWT_SECRET, then forwards those claims to Cube by
minting a NEW JWT signed with CUBE_API_SECRET (which Cube verifies and whose payload Cube exposes as
securityContext to check_auth / access_policy / query_rewrite).

Two secrets so the agent never holds Cube's secret; locally they default to the same value so a
single hand-minted token works end to end. The claim extraction at request time uses FastMCP's
get_http_headers() — the supported way to read HTTP headers from inside a tool (it bridges the HTTP
request into the tool context across the streamable-HTTP transport).
"""
from __future__ import annotations

import jwt
from fastmcp.server.dependencies import get_http_headers

from .config import settings


class AuthError(Exception):
    """Raised when the agent token is missing or fails validation."""


def extract_bearer(header_value: str) -> str:
    """Strip an optional 'Bearer ' prefix from an Authorization header value.

    Args:
        header_value (str): the raw Authorization header (may be empty or already bare).

    Returns:
        str: The token without the 'Bearer ' prefix, or '' if the header is empty.
    """
    if not header_value:
        return ""
    return header_value[7:] if header_value[:7].lower() == "bearer " else header_value


def decode_agent_token(token: str) -> dict:
    """Validate the agent JWT (HS256, AGENT_JWT_SECRET) and return its claims.

    Args:
        token (str): the bare JWT string presented by the agent.

    Returns:
        dict: The decoded JWT claims (level/domains/is_admin/sub/...).

    Raises:
        AuthError: if the token is missing, the secret is unset, or validation fails
            (bad signature, expired, malformed).
    """
    if not token:
        raise AuthError("missing token")
    if not settings.agent_jwt_secret:
        raise AuthError("agent_jwt_secret not configured")
    try:
        return jwt.decode(token, settings.agent_jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:  # invalid signature / expired / malformed
        raise AuthError(str(exc)) from exc


def claims_to_security_context(claims: dict) -> dict:
    """Project the agent claims onto the Cube security-context shape the gateway forwards.

    Only the access-relevant keys are passed through (cube.py:check_auth normalizes them).

    Args:
        claims (dict): the decoded agent-JWT claims.

    Returns:
        A dict with the subset of keys to forward: level, domains, is_admin, user_id
        (user_id is taken from `user_id` or, failing that, `sub`).
    """
    sc: dict = {}
    if "level" in claims:
        sc["level"] = claims["level"]
    if "domains" in claims:
        sc["domains"] = claims["domains"]
    if "is_admin" in claims:
        sc["is_admin"] = claims["is_admin"]
    uid = claims.get("user_id") or claims.get("sub")
    if uid is not None:
        sc["user_id"] = uid
    return sc


def current_security_context() -> dict:
    """Read the agent JWT from the current HTTP request and return the context to forward.

    Uses FastMCP's get_http_headers() to reach the request from inside a tool call.

    Returns:
        dict: The Cube security context (see claims_to_security_context), or {} with no HTTP
        context (e.g. unit tests calling the tools directly) or no/invalid token — in which case
        Cube applies its own fallback (admin if CUBE_DEFAULT_ADMIN=true, else least privilege).
    """
    try:
        headers = get_http_headers(include_all=True)
    except Exception:
        # No active HTTP request (tool called outside a request context).
        return {}
    token = extract_bearer((headers or {}).get("authorization", ""))
    if not token:
        return {}
    try:
        claims = decode_agent_token(token)
    except AuthError:
        return {}
    return claims_to_security_context(claims)
