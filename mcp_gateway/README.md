<!-- TECHNICAL METADATA — update on every change. -->
| Metadata | |
|---|---|
| **Created by** | data-platform@example.com |
| **Last modified by** | data-platform@example.com |
| **Last modified** | 2026-08-07 |
| **Status** | PoC · local-first (see root `README.md` / `CLAUDE.md`) |
| **Upstream** | Cube Core REST API (`/cubejs-api/v1`) on the same pod |

## Jira cards


### `CAA-629` — describe_view forwards view folders (2026-08-07)

`ViewDetail` gains `folders` (name + qualified members), forwarded 1:1 from `/meta`, and the
`describe_view` docstring instructs the agent to navigate wide views by folder (a folder name
can itself be a guardrail — "Valores por linha (não somar)"). Integration test asserts the
round trip on `subscription` (17 folders) and that every folder member exists in the view.

Append-only log — add one topic per card that touches this service (most recent first).

### (no card yet) — `segments` support in the tools (2026-07-27)

The gateway used to hide segments entirely: the models had no field for them and `_build_query` had
no parameter. Now `ViewSummary.segments` (names) and `ViewDetail.segments` (`MemberInfo`, so the
business definition travels with it) are populated from `/meta`, and `validate_query`/`run_query`
accept `segments`, normalized by `_norm_segments` — which also accepts a stringified array and a bare
string, the same client-serialization hazard that once broke `date_range`. `run_query`'s docstring now
opens its guidance with SEGMENTS FIRST: if a named segment matches the audience being asked for, use
it instead of rebuilding the condition. Tests cover the unit normalization, the round trip, and that a
segment travelling through a join filters instead of multiplying.

### (no card yet) — `describe_view` forwards view-level `meta` (2026-07-27)

`ViewDetail` gained a `meta` field, so view-level `meta.ai_context` now reaches the agent — until now
the gateway silently dropped it (only member-level `meta` was mapped, via `_member()`). `list_metrics`
was left lean on purpose: `ViewSummary` still returns names + description only, so the discovery step
does not pay the token cost of every view's prose. `run_query`/`validate_query` unchanged. Tests cover
the whole round trip (YAML → `/meta` → `ViewDetail`) plus member-level `meta`, so a refactor can't
drop either silently.

### `DSD-1594` — Forward the agent's role to Cube (2026-06-24)

The gateway now transports access claims. `TokenAuthMiddleware` validates a signed **agent JWT**
(`AGENT_JWT_SECRET`); `app/auth.py` reads it from the request (FastMCP `get_http_headers`) and
`cube_client._headers` mints the **Cube JWT** (`CUBE_API_SECRET`) whose payload is the security
context (`level/domains/is_admin/user_id`). No HTTP context / invalid token → empty claims (Cube
applies its fallback). New `config.agent_jwt_secret`. See `docs/access-control.md`.

### `DSD-1580` — English docstrings + existence routing (2026-06-16)

Tool docstrings and this README translated to English (standard: non-AI content in English; only Cube
member description/title/meta stay Portuguese). `run_query` gained the existence rule (absence in a
metric ≠ non-existence → check the catalog dimensions products/reps/customers). `date_range`/`filters`/
`order` are strongly typed + defensively normalized.

### `DSD-1576` — MCP Gateway (FastMCP/FastAPI) (2026-06-15)

Gateway scaffold connecting the AI to the semantic layer: FastAPI hosting a FastMCP server
(Streamable HTTP) that translates MCP tools into calls to the Cube REST API. 5 tools
(`list_metrics`, `describe_view`, `get_dimension_values`, `validate_query`, `run_query`),
views only (Cube's native governance boundary), service-token auth, `area=admin`. Tool params
are strongly typed with defensive normalization of `date_range`/`filters`/`order`.

---

# `mcp_gateway/` — MCP Gateway for the AI to query the Semantic Layer

Separate Python service that gives the AI governed access to the Cube Core metrics via **MCP**.
Cube Core has no native MCP (it's a Cube Cloud feature), so this gateway translates the agent's
tools into **REST API** calls to Cube. It runs as its **own container** — locally via
`docker-compose`, and as a **sidecar in the same pod** as Cube in a cluster (same budget, independent
lifecycle). Never inside the Cube container.

## Tools (what the AI can do)

| Tool | Purpose |
|---|---|
| `list_metrics` | Catalog: every view and its measures/dimensions (discovery). |
| `describe_view` | Detail of one view: types, format, and `meta` (granularity/additive/synonyms/prefer/avoid). |
| `get_dimension_values` | Valid values of a dimension (to build filters and check existence). |
| `validate_query` | Dry-run: validate and return the SQL, without executing. |
| `run_query` | Execute the query and return data **+ compiled SQL**. |

Only **views** are reachable: cubes are `public:false` and Cube hides them from `/meta`.

## Configuration

Copy `.env.example` to `.env`. Main variables: `CUBE_URL` (local `http://cube:4000`; sidecar
`http://localhost:4000`), `CUBE_API_SECRET` (same as Cube — the gateway signs a JWT with it),
`GATEWAY_TOKEN` (token the agent sends in `Authorization`; empty disables auth locally). Version
policy: pin (see `requirements.txt`).

## Run locally

```bash
# from the repo root, brings up Cube + gateway together
docker compose up --build
# healthcheck
curl http://localhost:8000/health
```

The MCP endpoint (Streamable HTTP) is **`http://localhost:8000/mcp`**.

## Connect an MCP client

- **Inspector:** `npx @modelcontextprotocol/inspector` -> connect to `http://localhost:8000/mcp`
  (header `Authorization: <GATEWAY_TOKEN>` if set).
- **Agent (Anthropic API / remote MCP):** point the URL `http://<host>:8000/mcp` + token.

## Tests

```bash
pip install -r requirements.txt
pytest            # integration tests skip if Cube is down (CUBE_URL); unit tests always run
```

## Architecture (summary)

```
AI agent ──MCP/HTTP──▶ mcp_gateway (FastAPI+FastMCP) ──REST──▶ Cube Core ──▶ warehouse
                        (views only, area=admin)       localhost:4000 (same pod)
```
Details and decisions: `CLAUDE.md` (Core != Cloud; gateway is a separate service) and card DSD-1576.
