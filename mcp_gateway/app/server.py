"""MCP server: tools the LLM agent uses to query the Cube semantic layer.

Design (market pattern: small, intent-driven tool set -> fewer wrong picks):
  list_metrics -> describe_view -> get_dimension_values -> validate_query -> run_query.
Only VIEWS are visible: Cube hides public:false cubes from /meta, so the governance
boundary is native — the gateway never targets cubes.

Parameters are STRONGLY TYPED so the MCP client serializes them correctly. An untyped
param (e.g. `Any`) produced a weak JSON schema, and the model sent `date_range` as a
stringified array — which Cube silently accepted and collapsed to a single bucket (no
error, wrong result). To be safe we ALSO normalize defensively (`_norm_*`), so a
stringified array/object is parsed back before reaching Cube.

Language: tool docstrings/descriptions are in ENGLISH (operational docs, not business
semantics); the Portuguese business descriptions live on the Cube members (served via /meta).
The docstring language does not affect the agent's reply language.

The tool functions are plain async functions registered with mcp.tool(...) so they stay
directly callable from tests.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from .cube_client import CubeClient

mcp = FastMCP(name="fq-cube-gateway")

# Cube client is injected at app startup (see main.lifespan).
_client: CubeClient | None = None

Granularity = Literal["day", "week", "month", "quarter", "year"]


def set_client(client: CubeClient) -> None:
    """Inject the Cube client used by the tools (called at app startup).

    Args:
        client (CubeClient): the CubeClient instance to use.
    """
    global _client
    _client = client


def _require_client() -> CubeClient:
    """Return the injected Cube client.

    Returns:
        CubeClient: The active CubeClient.

    Raises:
        RuntimeError: if no client has been set yet.
    """
    if _client is None:
        raise RuntimeError("Cube client not initialized")
    return _client


# --------------------------------------------------------------------- models
class MemberInfo(BaseModel):
    """One measure, dimension or segment as the agent sees it."""

    name: str = Field(description="Qualified name (view.member) used in queries.")
    title: str | None = None
    description: str | None = None
    type: str | None = Field(default=None,
                             description="Member type (number, string, time, boolean).")
    agg_type: str | None = Field(default=None,
                                 description="Measure aggregation (sum, count, avg, max...).")
    format: str | None = None
    meta: dict | None = Field(
        default=None,
        description="Hints for the agent: granularity, additive, synonyms, prefer, avoid.")


class ViewSummary(BaseModel):
    """A view reduced to its name and its member names — the discovery payload."""

    name: str
    title: str | None = None
    description: str | None = None
    measures: list[str]
    dimensions: list[str]
    segments: list[str] = Field(default_factory=list, description=(
        "Named, governed row filters. Applying one by name is safer than rebuilding its condition "
        "with filters."))


class FolderInfo(BaseModel):
    """A named thematic group of members inside a view."""

    name: str = Field(description="Thematic group label (Portuguese).")
    members: list[str] = Field(description="Qualified member names grouped under this folder.")


class ViewDetail(BaseModel):
    """A view with every member described, plus its folders and view-level meta."""

    name: str
    title: str | None = None
    description: str | None = None
    meta: dict | None = Field(default=None, description=(
        "View-level hints for the agent. `ai_context` carries how to READ a value (present / "
        "zero / missing row), what to ask back when the question is ambiguous, and "
        "prohibitions with their "
        "quantified reason."))
    measures: list[MemberInfo]
    dimensions: list[MemberInfo]
    segments: list[MemberInfo] = Field(default_factory=list, description=(
        "Named row filters with their business definition. The description states the cut AND what "
        "falls outside it."))
    folders: list[FolderInfo] = Field(default_factory=list, description=(
        "Thematic map of the view's members (wide views group ~15 blocks: medidas, eixos de tempo, "
        "chaves, PII...). Navigate by folder to pick members; a folder name can carry a guardrail "
        "(e.g. 'Valores por linha (não somar)')."))


class Filter(BaseModel):
    """One Cube filter: a member, an operator, and the values it compares against."""

    member: str = Field(description="Qualified member (view.dimension) to filter on.")
    operator: str = Field(description=(
        "Cube operator: equals, notEquals, contains, notContains, startsWith, endsWith, "
        "gt, gte, lt, lte, set, notSet, inDateRange, notInDateRange, beforeDate, afterDate."))
    values: list[str] | None = Field(
        default=None, description="Values (list of strings). Omit for set/notSet.")


class QueryResult(BaseModel):
    """The rows a query returned, with the SQL that produced them."""

    data: list[dict]
    row_count: int
    sql: str | None = Field(default=None, description="Compiled SQL that Cube ran.")
    query: dict = Field(description="The Cube query actually sent.")


class ValidationResult(BaseModel):
    """The outcome of a dry run: whether the query compiles, and the SQL it would run."""

    valid: bool
    error: str | None = None
    sql: str | None = None


# --------------------------------------------------------------------- helpers
def _views(meta: dict) -> list[dict]:
    """Filter a /meta response down to the public views.

    Args:
        meta (dict): a /meta response.

    Returns:
        list[dict]: The list of members whose type is 'view'.
    """
    return [c for c in meta.get("cubes", []) if c.get("type") == "view"]


def _member(m: dict) -> MemberInfo:
    """Map a raw /meta member dict to a MemberInfo model.

    Args:
        m (dict): a measure/dimension dict from /meta.

    Returns:
        MemberInfo: The corresponding MemberInfo.
    """
    return MemberInfo(
        name=m.get("name"),
        title=m.get("title") or m.get("shortTitle"),
        description=m.get("description"),
        type=m.get("type"),
        agg_type=m.get("aggType"),
        format=m.get("format"),
        meta=m.get("meta") or None,
    )


def _maybe_json(value: Any) -> Any:
    """Parse a value that arrived as a JSON-looking string, leaving anything else untouched.

    Defends against MCP clients that serialize complex arguments as strings.

    Args:
        value (Any): The raw argument as received.

    Returns:
        Any: The parsed object when the string held JSON, otherwise the value unchanged.
    """
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in ("[", "{"):
            try:
                return json.loads(s)
            except Exception:
                return value
    return value


def _norm_date_range(date_range: Any) -> Any:
    """Normalize a `date_range` argument.

    It is either a relative string ("last 30 days") or a [start, end] array; a stringified array
    is parsed back to a real list — the bug that silently collapsed a multi-month range to one row.

    Args:
        date_range (Any): The raw date_range argument.

    Returns:
        Any: The relative string, the [start, end] list, or None when absent.
    """
    if date_range is None:
        return None
    return _maybe_json(date_range)


def _norm_order(order: Any) -> dict | None:
    """Normalize an `order` argument, parsing a stringified dict/list back to an object.

    Args:
        order (Any): the raw order argument (dict, list, JSON string, or None).

    Returns:
        Optional[dict]: The order as a dict/list, or None if absent/invalid.
    """
    if order is None:
        return None
    order = _maybe_json(order)
    return order if isinstance(order, (dict, list)) else None


def _filters_to_list(filters: Any) -> list[dict]:
    """Normalize the `filters` argument into a list of Cube filter dicts.

    Accepts Filter models, dicts, or JSON strings and drops None values.

    Args:
        filters (Any): the raw filters argument.

    Returns:
        list[dict]: A list of Cube filter dicts.
    """
    out: list[dict] = []
    for f in filters or []:
        if isinstance(f, Filter):
            out.append(f.model_dump(exclude_none=True))
        elif isinstance(f, str):
            parsed = _maybe_json(f)
            if isinstance(parsed, dict):
                out.append(parsed)
        elif isinstance(f, dict):
            out.append({k: v for k, v in f.items() if v is not None})
    return out


def _norm_segments(segments: Any) -> list[str]:
    """Normalize the `segments` argument into a list of qualified segment names.

    Accepts a real list, a stringified JSON array, or a single name as a bare string — the same
    defensive handling that `date_range` needed when a client serialized an array as a string.

    Args:
        segments (Any): the raw segments argument.

    Returns:
        list[str]: A list of segment names (empty when absent).
    """
    if not segments:
        return []
    segments = _maybe_json(segments)
    if isinstance(segments, str):
        return [segments]
    if isinstance(segments, list):
        return [s for s in segments if isinstance(s, str)]
    return []


def _build_query(
    measures: list[str] | None,
    dimensions: list[str] | None,
    time_dimension: str | None,
    granularity: str | None,
    date_range: Any,
    filters: Any,
    order: Any,
    limit: int,
    segments: Any = None,
) -> dict:
    """Assemble a Cube query dict from the individual tool arguments.

    Args:
        measures (Optional[list[str]]): measure names.
        dimensions (Optional[list[str]]): dimension names.
        time_dimension (Optional[str]): the time dimension, if any.
        granularity (Optional[str]): the time granularity, if any.
        date_range (Any): a relative string or [start, end] array.
        filters (Any): the raw filters argument (see _filters_to_list).
        order (Any): the raw order argument (see _norm_order).
        limit (int): the row limit.
        segments (Any): the raw segments argument (see _norm_segments).

    Returns:
        dict: The assembled Cube query dict.
    """
    q: dict = {}
    if measures:
        q["measures"] = list(measures)
    if dimensions:
        q["dimensions"] = list(dimensions)
    segs = _norm_segments(segments)
    if segs:
        q["segments"] = segs
    if time_dimension:
        td: dict = {"dimension": time_dimension}
        if granularity:
            td["granularity"] = granularity
        dr = _norm_date_range(date_range)
        if dr:
            td["dateRange"] = dr
        q["timeDimensions"] = [td]
    if filters:
        fl = _filters_to_list(filters)
        if fl:
            q["filters"] = fl
    o = _norm_order(order)
    if o:
        q["order"] = o
    q["limit"] = limit
    return q


# ----------------------------------------------------------------------- tools
async def list_metrics() -> list[ViewSummary]:
    """List the metric catalog.

    Returns every governed view with its measures, dimensions and segments (qualified names).

    Use this as the FIRST step to discover what exists before building a query. Only public views
    are returned (private cubes never appear). `segments` are named, governed row filters — prefer
    one over a hand-built filter whenever it matches what is being asked. For the full detail of a
    view (types, format, aggregation hints, meta.ai_context) use describe_view.

    Returns:
        list[ViewSummary]: One entry per public view, with its qualified member names.
    """
    meta = await _require_client().meta()
    out = []
    for v in _views(meta):
        out.append(ViewSummary(
            name=v.get("name"),
            title=v.get("title"),
            description=v.get("description"),
            measures=[m.get("name") for m in v.get("measures", [])],
            dimensions=[d.get("name") for d in v.get("dimensions", [])],
            segments=[s.get("name") for s in v.get("segments", [])],
        ))
    return out


async def describe_view(view: str) -> ViewDetail:
    """Describe one view in full.

    Read its view-level `meta.ai_context` FIRST — it states how to interpret values, when to ask
    the user to disambiguate, and what never to do. Then each measure (type, aggregation, format,
    and meta with granularity/additive/synonyms/prefer/avoid) and each dimension (type).

    On wide views, use `folders` as the map: members come grouped in thematic blocks (medidas,
    eixos de tempo, chaves, PII...) and a folder name can itself be a guardrail ('Valores por linha
    (não somar)'). Use it to pick the right members and to check whether a measure is additive
    before aggregating. `view` is a name returned by list_metrics (e.g. 'sales'). Member
    descriptions are in Portuguese (business semantics).

    Args:
        view (str): The view name, as returned by list_metrics.

    Returns:
        ViewDetail: The view with its members, folders and metadata.

    Raises:
        ValueError: If no public view carries that name.
    """
    meta = await _require_client().meta()
    v = next((c for c in _views(meta) if c.get("name") == view), None)
    if v is None:
        raise ValueError(f"View '{view}' not found. Use list_metrics to see valid views.")
    return ViewDetail(
        name=v.get("name"),
        title=v.get("title"),
        description=v.get("description"),
        meta=v.get("meta") or None,
        measures=[_member(m) for m in v.get("measures", [])],
        dimensions=[_member(d) for d in v.get("dimensions", [])],
        segments=[_member(s) for s in v.get("segments", [])],
        folders=[FolderInfo(name=f.get("name", ""), members=f.get("members", []))
                 for f in v.get("folders", []) or []],
    )


async def get_dimension_values(dimension: str, limit: int = 100) -> list[Any]:
    """List the distinct values of a dimension (e.g.

    'sales.product_line') so the agent can build filters with VALID values instead of inventing
    them — and to check whether an entity EXISTS. `dimension` is the qualified name
    (view.dimension). Avoid high-cardinality dimensions (ids).

    Args:
        dimension (str): The qualified dimension name (view.dimension).
        limit (int): Maximum number of distinct values to return.

    Returns:
        list[Any]: The distinct values, ascending.
    """
    q = {"dimensions": [dimension], "order": {dimension: "asc"}, "limit": limit}
    body = await _require_client().load(q)
    rows = body.get("data", [])
    return [r.get(dimension) for r in rows]


async def validate_query(
    measures: list[str] | None = None,
    dimensions: list[str] | None = None,
    time_dimension: str | None = None,
    granularity: Granularity | None = None,
    date_range: str | list[str] | None = None,
    filters: list[Filter] | None = None,
    order: dict[str, str] | None = None,
    limit: int = 1000,
    segments: list[str] | None = None,
) -> ValidationResult:
    """Validate a query WITHOUT executing it (dry-run) and return the compiled SQL.

    Use before run_query when unsure about members/filters. Members are qualified (view.member).
    Formats: - `date_range`: relative string ('last 30 days', 'this month', 'this year') OR an
    array ['YYYY-MM-DD','YYYY-MM-DD'] — send a real array, not a string. - `filters`: list of
    objects {member, operator, values}. - `segments`: list of segment names (see run_query). -
    `granularity`: day | week | month | quarter | year. - `order`: object {'view.member':
    'asc'|'desc'}.

    Args:
        measures (Optional[list[str]]): Qualified measure names (view.measure).
        dimensions (Optional[list[str]]): Qualified dimension names to group by.
        time_dimension (Optional[str]): Qualified time dimension to slice over.
        granularity (Optional[str]): day | week | month | quarter | year.
        date_range (Any): Relative string, or a ['YYYY-MM-DD','YYYY-MM-DD'] array.
        filters (Any): List of {member, operator, values} objects.
        order (Any): Object mapping a qualified member to 'asc' or 'desc'.
        limit (int): Maximum number of rows the query would return.
        segments (Optional[list[str]]): Named governed row filters to apply.

    Returns:
        ValidationResult: Whether the query compiles, plus the SQL it would run.
    """
    client = _require_client()
    q = _build_query(measures, dimensions, time_dimension, granularity, date_range, filters, order,
                     limit, segments)
    valid, error = await client.dry_run(q)
    sql = await client.sql(q) if valid else None
    return ValidationResult(valid=valid, error=error, sql=sql)


async def run_query(
    measures: list[str] | None = None,
    dimensions: list[str] | None = None,
    time_dimension: str | None = None,
    granularity: Granularity | None = None,
    date_range: str | list[str] | None = None,
    filters: list[Filter] | None = None,
    order: dict[str, str] | None = None,
    limit: int = 1000,
    segments: list[str] | None = None,
) -> QueryResult:
    """Run a governed query against the semantic layer and return the data + the compiled SQL.

    Members are qualified (view.member), only from views (list_metrics/describe_view). Formats: -
    `date_range`: relative string ('last 30 days', 'this month', 'this year') OR an array ['YYYY-
    MM-DD','YYYY-MM-DD'] — send a real array, NOT a stringified array. - `filters`: list of objects
    {member, operator, values} (operator: equals, contains, gt, gte, lt, lte, set, notSet,
    inDateRange, beforeDate, afterDate...). - `segments`: list of segment names, e.g.
    ['sales.new_customers'] — send a real array.
    - `granularity`: day | week | month | quarter | year
    (use with time_dimension). - `order`: object {'view.member': 'desc'}.

    Guidance: - SEGMENTS FIRST: if a named segment matches the audience/state being asked for, USE
    IT instead of rebuilding the condition with filters — the segment is the governed definition
    and cannot be mis-specified. Read its description in describe_view: it states the cut AND what
    falls outside it. Several segments in one query are AND-ed, and they combine with filters and
    time_dimension. Never reimplement a segment's condition by hand. - Do NOT sum non-additive
    measures (check meta.additive via describe_view); for time slices prefer time_dimension +
    date_range over manual date filters. - EXISTENCE: if an entity (product, category, sales rep,
    customer) is ABSENT from a result, do NOT conclude it doesn't exist. Verify in the catalog
    dimension first — `products` (product/ category), `reps` (sales rep), `customers` (customer).
    Absence in a metric (e.g. commissions) means 'no data for that metric', not non-existence.

    Args:
        measures (Optional[list[str]]): Qualified measure names (view.measure).
        dimensions (Optional[list[str]]): Qualified dimension names to group by.
        time_dimension (Optional[str]): Qualified time dimension to slice over.
        granularity (Optional[str]): day | week | month | quarter | year.
        date_range (Any): Relative string, or a ['YYYY-MM-DD','YYYY-MM-DD'] array.
        filters (Any): List of {member, operator, values} objects.
        order (Any): Object mapping a qualified member to 'asc' or 'desc'.
        limit (int): Maximum number of rows to return.
        segments (Optional[list[str]]): Named governed row filters to apply.

    Returns:
        QueryResult: The rows, the row count, and the SQL Cube ran.
    """
    client = _require_client()
    q = _build_query(measures, dimensions, time_dimension, granularity, date_range, filters, order,
                     limit, segments)
    body = await client.load(q)
    data = body.get("data", [])
    sql = await client.sql(q)
    return QueryResult(data=data, row_count=len(data), sql=sql, query=q)


for _fn in (list_metrics, describe_view, get_dimension_values, validate_query, run_query):
    mcp.tool(_fn)
