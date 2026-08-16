"""Cube Core runtime configuration (config in Python, to match the rest of the stack).

Access-control model (two axes + override) — single source of truth in rbac.py; full design in
docs/access-control.md:
  - is_admin: total override (sees everything, ignores level/domain).
  - level:    table/view breadth (a view's min_level gates visibility).
  - domains:  which business domains; on the transversal commercial views the domain becomes a
              ROW filter on product_line. The "*" wildcard means all domains (no row filter).

This file resolves the security context from the JWT (check_auth), maps it to roles
(context_to_groups), enforces the ROW filter (query_rewrite) and isolates the cache per scope
(context_to_app_id). View VISIBILITY is enforced declaratively by access_policy in the view YAML.

The @config option names follow snake_case in Cube's Python config — verify against the installed
version (cube.dev/docs/config).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys

from cube import config

# Cube execs this file without its directory on sys.path, so the sibling import below
# (rbac.py, mounted/baked next to cube.py at /cube/conf) needs the dir added first.
try:
    _CONF_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:  # __file__ may be absent depending on how Cube loads the config
    _CONF_DIR = os.getcwd()
for _p in (_CONF_DIR, "/cube/conf"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import rbac  # noqa: E402  (must follow the sys.path setup above)


def _scope_key(ctx: dict) -> str:
    """Build the cache key identifying one SECURITY SCOPE.

    The compiled model and the query cache must not leak across scopes, so the key folds in
    is_admin + level + the sorted domain set.

    Args:
        ctx (dict): The request context Cube passes in, carrying `securityContext`.

    Returns:
        str: A stable key of the form "<admin>_<level>_<domains>".
    """
    sec = (ctx or {}).get('securityContext', {}) or {}
    admin = '1' if sec.get('is_admin') else '0'
    level = sec.get('level', 0)
    domains = ','.join(sorted(sec.get('domains') or []))
    return f"{admin}_{level}_{domains}"


@config('context_to_app_id')
def context_to_app_id(ctx: dict) -> str:
    """Return the per-scope id for the compiled data model.

    Args:
        ctx (dict): The request context Cube passes in.

    Returns:
        str: The app id; distinct scopes never share a compiled model.
    """
    return f"CUBE_APP_{_scope_key(ctx)}"


@config('context_to_orchestrator_id')
def context_to_orchestrator_id(ctx: dict) -> str:
    """Return the per-scope id for the query orchestrator and its cache.

    Args:
        ctx (dict): The request context Cube passes in.

    Returns:
        str: The orchestrator id; distinct scopes never share cached results.
    """
    return f"CUBE_ORCH_{_scope_key(ctx)}"


@config('context_to_groups')
def context_to_groups(ctx: dict) -> list[str]:
    """Resolve the security context into access_policy ROLES.

    Cube's access_policy `condition` parser is very limited (no comparisons, no `in`, no chained
    method/or), so all the access logic (level AND domain) is computed here in real Python and
    exposed as role strings; the view policies then match on `role:` alone, with no conditions.
    Two role families:
      t<band>            transversal view at min_level <band> (level gate only; the per-domain
                         row filter is applied by query_rewrite)
      d_<domain>_<band>  domain-specific view for <domain> at min_level <band>
    Admin and the "*" wildcard expand to every domain; admin clears every band.

    Args:
        ctx (dict): The request context Cube passes in, carrying `securityContext`.

    Returns:
        list[str]: The group names this caller holds, matched by each view's access_policy.
    """
    sec = (ctx or {}).get('securityContext', {}) or {}
    is_admin = bool(sec.get('is_admin'))
    try:
        level = int(sec.get('level') or 0)
    except (TypeError, ValueError):
        level = 0
    domains = sec.get('domains') or []

    eff_level = max(rbac.LEVELS.values()) if is_admin else level
    if is_admin or rbac.has_wildcard(domains):
        eff_domains = list(rbac.DOMAINS)
    else:
        eff_domains = [d for d in domains if d in rbac.DOMAINS]

    roles: list[str] = ['admin'] if is_admin else []
    for band in sorted(set(rbac.LEVELS.values())):
        if eff_level >= band:
            roles.append(f't{band}')
            for d in eff_domains:
                roles.append(f'd_{d}_{band}')
    return roles


# Scheduled refresh contexts — ACTIVE. Multitenancy keys the compiled model and the cache per
# security scope, so the background refresh worker needs one securityContext per scope it should
# materialize. Pre-aggregations are declared on the transversal cubes, and the admin scope covers
# them all (no row filter), so one context is enough: the rollups are built once, unfiltered, and
# every narrower scope reads through the same Cube Store partitions.
@config('scheduled_refresh_contexts')
def scheduled_refresh_contexts() -> list[dict]:
    """Security scopes the refresh worker materializes pre-aggregations for.

    Returns:
        list[dict]: A list with one admin securityContext (all domains, top level).
    """
    return [{'securityContext': {'is_admin': True,
                                 'level': max(rbac.LEVELS.values()),
                                 'domains': [rbac.WILDCARD_DOMAIN]}}]


def _referenced_views(query: dict) -> set[str]:
    """Collect the view names a query references.

    Args:
        query (dict): The incoming Cube query.

    Returns:
        set[str]: The prefix before the dot of every measure/dimension/timeDimension member.
    """
    members: list = []
    members += query.get('measures') or []
    members += query.get('dimensions') or []
    for td in query.get('timeDimensions') or []:
        if isinstance(td, dict) and td.get('dimension'):
            members.append(td['dimension'])
    return {m.split('.', 1)[0] for m in members if isinstance(m, str) and '.' in m}


@config('query_rewrite')
def query_rewrite(query: dict, ctx: dict) -> dict:
    """Apply Row-Level Security to an incoming query.

    Injects `product_line IN <the lines the caller's domains unlock>` on every transversal
    commercial view the query references.

    Admins and "*" wildcard holders are not filtered; a non-admin whose domains map to no category
    gets a deny-all filter rather than a leak. Composes (AND) with any access_policy row rules;
    view VISIBILITY is handled by access_policy.

    Args:
        query (dict): The incoming Cube query, mutated in place.
        ctx (dict): The request context carrying `securityContext`.

    Returns:
        dict: The query with the row filter appended (unchanged for admin/wildcard callers).
    """
    sec = (ctx or {}).get('securityContext', {}) or {}
    if sec.get('is_admin') or rbac.has_wildcard(sec.get('domains')):
        return query

    categories = rbac.domains_to_categories(sec.get('domains'))
    for view in _referenced_views(query):
        if view not in rbac.TRANSVERSAL_VIEWS_WITH_CATEGORY:
            continue
        values = categories if categories else ['__no_access__']
        query.setdefault('filters', []).append({
            'member': f"{view}.{rbac.CATEGORY_DIMENSION}",
            'operator': 'equals',
            'values': values,
        })
    return query


def _b64url_decode(seg: str) -> bytes:
    """Decode a base64url segment, restoring the stripped `=` padding.

    Args:
        seg (str): The base64url segment taken from a JWT.

    Returns:
        bytes: The decoded bytes.
    """
    return base64.urlsafe_b64decode(seg + '=' * (-len(seg) % 4))


def _decode_jwt_hs256(token: str, secret: str) -> dict:
    """Verify an HS256 JWT signature and return its payload.

    Args:
        token (str): The encoded JWT.
        secret (str): The shared signing secret.

    Returns:
        dict: The decoded payload claims.

    Raises:
        ValueError: If the token is malformed, uses another algorithm, or fails verification.
    """
    parts = token.split('.')
    if len(parts) != 3:
        raise ValueError('not a JWT')
    header_b64, payload_b64, sig_b64 = parts
    header = json.loads(_b64url_decode(header_b64))
    if header.get('alg') != 'HS256':
        raise ValueError('unexpected alg')
    signing_input = f'{header_b64}.{payload_b64}'.encode()
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
        raise ValueError('bad signature')
    return json.loads(_b64url_decode(payload_b64))


def _fallback_context() -> dict:
    """Build the security context used when no valid token is presented.

    Admin when CUBE_DEFAULT_ADMIN=true (local convenience), otherwise least privilege — so a
    misconfigured deployment denies rather than exposes.

    Returns:
        dict: The fallback security context.
    """
    if os.getenv('CUBE_DEFAULT_ADMIN', 'false').lower() == 'true':
        return {
            'is_admin': True,
            'level': max(rbac.LEVELS.values()),
            'domains': [rbac.WILDCARD_DOMAIN],
            'user_id': None,
        }
    return {'is_admin': False, 'level': 0, 'domains': [], 'user_id': None}


@config('check_auth')
def check_auth(ctx: dict, token: str) -> dict:
    """Resolve and normalize the security context from the JWT.

    Because a custom check_auth is defined, Cube does NOT verify/decode the token itself — we verify
    the HS256 signature against CUBEJS_API_SECRET with the stdlib (no PyJWT in the Cube image) and
    read the claims. Expected payload (minted by the MCP gateway from the agent's signed token):
    {"level": int, "domains": [str], "is_admin": bool, "user_id"/"sub": str}. Anything that fails
    to verify or carries no access claims falls back to _fallback_context().

    Args:
        ctx (dict): The request context Cube passes in.
        token (str): The raw Authorization header value.

    Returns:
        dict: {"security_context": {...}} — the normalized claims Cube stores for the request.
    """
    secret = os.getenv('CUBEJS_API_SECRET', '') or ''
    claims = None
    if token and secret:
        try:
            claims = _decode_jwt_hs256(token, secret)
        except Exception:
            claims = None

    if not claims or not any(k in claims for k in ('level', 'domains', 'is_admin')):
        return {'security_context': _fallback_context()}

    try:
        level = int(claims.get('level')) if claims.get('level') is not None else 0
    except (TypeError, ValueError):
        level = 0
    domains = claims.get('domains')
    if not isinstance(domains, list):
        domains = []

    return {'security_context': {
        'is_admin': bool(claims.get('is_admin', False)),
        'level': level,
        'domains': domains,
        'user_id': claims.get('user_id') or claims.get('sub'),
    }}
