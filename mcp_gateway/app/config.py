"""Runtime config for the MCP gateway (env-driven, no external settings dep)."""
from __future__ import annotations

import os


class Settings:
    """Environment-driven settings, read once at import time."""

    # Cube REST API base (no trailing /cubejs-api/v1 — added by the client).
    cube_url: str = os.getenv("CUBE_URL", "http://localhost:4000")
    # Shared Cube secret; the client signs the Cube JWT with it (Cube verifies in non-dev mode).
    cube_api_secret: str = os.getenv("CUBE_API_SECRET", "") or os.getenv("CUBEJS_API_SECRET", "")
    # Secret that signs the AGENT's JWT (the one the agent presents to the gateway). Kept
    # separate from cube_api_secret so the agent never holds Cube's secret; locally it
    # defaults to the Cube secret so a single token works end to end. When set, /mcp requires
    # a valid agent JWT and the gateway forwards its claims (level/domains/is_admin) to Cube.
    agent_jwt_secret: str = (
        os.getenv("AGENT_JWT_SECRET", "")
        or os.getenv("CUBE_API_SECRET", "")
        or os.getenv("CUBEJS_API_SECRET", "")
    )
    # Legacy static service token (pre-JWT). Used only when agent_jwt_secret is unset.
    gateway_token: str = os.getenv("GATEWAY_TOKEN", "")
    host: str = os.getenv("MCP_HOST", "0.0.0.0")
    port: int = int(os.getenv("MCP_PORT", "8000"))
    # Seconds to keep retrying Cube's async "Continue wait" before giving up.
    query_timeout: int = int(os.getenv("CUBE_QUERY_TIMEOUT", "180"))


settings = Settings()
