"""FastAPI host for the MCP gateway.

Serves the FastMCP server over Streamable HTTP at /mcp, plus a /health check. Lifespan wires the
Cube client into the MCP tools and runs the FastMCP session manager. A middleware authenticates
/mcp: a signed agent JWT (AGENT_JWT_SECRET) when configured, else the legacy GATEWAY_TOKEN.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import auth
from . import server as srv
from .config import settings
from .cube_client import CubeClient

# ASGI app for the MCP server (Streamable HTTP transport), exposed at path /mcp.
mcp_app = srv.mcp.http_app(path="/mcp")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Wire the Cube client into the MCP tools and run FastMCP's session manager.

    Args:
        app (FastAPI): the FastAPI application being started.

    Yields:
        None: Control for the app's lifespan; the Cube client is closed on shutdown.
    """
    client = CubeClient(settings.cube_url, settings.cube_api_secret, settings.query_timeout)
    srv.set_client(client)
    # Run FastMCP's own lifespan (session manager) nested inside ours.
    async with mcp_app.lifespan(app):
        yield
    await client.aclose()


app = FastAPI(title="Semantic Layer MCP Gateway", version="0.1.0", lifespan=lifespan)


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Gate /mcp at the HTTP boundary.

    Preferred: a signed agent JWT (AGENT_JWT_SECRET) whose claims the gateway forwards to Cube (see
    cube_client._headers / app.auth). Falls back to a legacy static token (GATEWAY_TOKEN) when no
    JWT secret is configured. If neither is set, auth is disabled (local only).
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Authenticate /mcp requests before passing them on.

        Args:
            request (Request): the incoming Starlette request.
            call_next (Callable): the downstream ASGI handler.

        Returns:
            Response: A 401 JSONResponse when authentication fails, else the downstream response.
        """
        if request.url.path.startswith("/mcp"):
            header = request.headers.get("authorization", "")
            token = auth.extract_bearer(header)
            if settings.agent_jwt_secret:
                try:
                    auth.decode_agent_token(token)
                except auth.AuthError:
                    return JSONResponse({"error": "unauthorized"}, status_code=401)
            elif settings.gateway_token and token != settings.gateway_token:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


app.add_middleware(TokenAuthMiddleware)


@app.get("/health")
async def health() -> dict:
    """Liveness probe.

    Returns:
        dict: A small status dict including the configured Cube URL.
    """
    return {"status": "ok", "cube_url": settings.cube_url}


# Mount the MCP ASGI app last; /health (explicit route) still resolves first.
app.mount("/", mcp_app)
