"""MCP client for the Confluent Real-Time Context Engine (RTCE).

RTCE serves materialized topic data to AI agents over an MCP endpoint:

    https://mcp.<REGION>.aws.confluent.cloud/mcp/v1/context-engine
        /organizations/<ORG_ID>/environments/<ENV_ID>/kafka-clusters/<LKC_ID>

Auth is HTTP Basic with a **Global** Confluent Cloud API key:
``Authorization: Basic base64(<KEY>:<SECRET>)``. Once connected, RTCE exposes
three MCP tools. Current endpoints expose ``query_data``, ``list_topics``, and
``get_metadata``; older endpoints used camelCase names. The client calls
``list_tools`` when it connects and maps either spelling at runtime.

``query_data`` additionally *requires*
``max_result_rows`` (see ``MAX_RESULT_ROWS``) — it is not optional, and omitting
it fails the same opaque way. Ask a live endpoint what it exposes with
``session.list_tools()`` rather than trusting any of these names, including
these.

This wrapper speaks the streamable-HTTP MCP transport via the official ``mcp``
SDK and parses ``queryData`` results into plain row dicts. It is deliberately
tolerant of the exact result envelope (list vs. ``{"rows": [...]}`` etc.), since
that is the part most likely to need validation against a live endpoint — use
``f1-social-feed-rtce --probe`` to confirm the contract before relying on it.

Docs: https://docs.confluent.io/cloud/current/ai/real-time-context-engine/get-started.html
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger("f1-social-feed-rtce.client")

# RTCE's own cap on queryData.max_result_rows — the schema says "max 200" and the
# argument is REQUIRED, not optional. Omitting it fails the call.
MAX_RESULT_ROWS = 200


def basic_token(api_key: str, api_secret: str) -> str:
    """Base64 ``key:secret`` for the RTCE ``Authorization: Basic`` header."""
    return base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()


def build_endpoint(region: str, org_id: str, env_id: str, cluster_id: str) -> str:
    """Construct the RTCE MCP endpoint URL from its component IDs."""
    return (
        f"https://mcp.{region}.aws.confluent.cloud/mcp/v1/context-engine"
        f"/organizations/{org_id}/environments/{env_id}/kafka-clusters/{cluster_id}"
    )


def _coerce_scalar(value: Any, sql_type: str) -> Any:
    """Cast one RTCE cell to a Python value using its declared column type.

    Every cell arrives as a **string** — ``"88"``, ``"99.4"``, ``"TRUE"``. That
    matters more than it looks: ``FeedState`` does ``bool(record["anomaly_..."])``
    and ``position < last_position``, and the string ``"FALSE"`` is truthy while
    ``"10" < "9"`` is True. Untyped rows don't fail loudly, they just make the
    feed wrong.
    """
    if value is None or value == "":
        return None
    upper = sql_type.upper()
    try:
        if "BOOLEAN" in upper:
            return str(value).strip().upper() == "TRUE"
        if "INT" in upper:  # INTEGER, BIGINT, SMALLINT
            return int(value)
        if any(t in upper for t in ("DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL")):
            return float(value)
    except (TypeError, ValueError):
        return value
    return value


def _coerce_rows(data: Any) -> list[dict]:
    """Pull a list of row dicts out of whatever shape queryData returned.

    The live envelope is::

        {"status": "success",
         "rows": {"schema": {"columns": [{"name": "CAR_NUMBER", "type": {...}}, ...]},
                  "data":   [{"row": ["88", "9", "99.4", ...]}, ...]}}

    So rows are **positional arrays**, not objects — they only become records
    once zipped with ``schema.columns``. RTCE also **upper-cases** column names
    (``CAR_NUMBER``), while everything downstream — ``FeedState``, the Kafka-backed
    ``f1-social-feed`` it shares an API with, and the Avro schema itself — uses
    lower case, so names are folded back down here. Note ``position`` comes back
    already lower-cased, presumably because it's reserved; lowering everything
    makes that difference moot instead of a special case.
    """
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if not isinstance(data, dict):
        return []

    rows = data.get("rows")
    if isinstance(rows, dict):
        columns = (rows.get("schema") or {}).get("columns") or []
        names = [str(c.get("name", "")).lower() for c in columns]
        types = [str((c.get("type") or {}).get("type", "")) for c in columns]
        records = []
        for entry in rows.get("data") or []:
            values = entry.get("row") if isinstance(entry, dict) else entry
            if not isinstance(values, list):
                continue
            records.append(
                {
                    name: _coerce_scalar(value, sql_type)
                    for name, sql_type, value in zip(names, types, values, strict=False)
                }
            )
        return records

    for key in ("rows", "data", "results", "records", "items"):
        value = data.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
    return []


class RTCEQueryError(RuntimeError):
    """RTCE accepted the call but rejected the query.

    A failed query is NOT an MCP error — it comes back as a normal successful
    tool result whose JSON body is ``{"status": "error", "error": {...}}``. Since
    that body is a dict, ``_coerce_rows`` used to hand it back as a single data
    row, so a bad query looked to the poller exactly like one row of race data
    and the feed silently served nothing. Raise instead.
    """


def _raise_if_error(payload: Any) -> None:
    if isinstance(payload, dict) and payload.get("status") == "error":
        err = payload.get("error") or {}
        raise RTCEQueryError(f"{err.get('code', 'RTCE_ERROR')}: {err.get('message', payload)}")


def _rows_from_result(result: Any) -> list[dict]:
    """Extract row dicts from an MCP ``CallToolResult`` (JSON in text blocks)."""
    rows: list[dict] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            logger.debug("non-JSON content block from RTCE: %.120s", text)
            continue
        _raise_if_error(payload)
        rows.extend(_coerce_rows(payload))
    return rows


class RTCEClient:
    """Thin async MCP client bound to one cluster's RTCE endpoint.

    Each call opens a fresh streamable-HTTP session (initialize → call_tool →
    close). At poll cadences of seconds this is simpler and more robust than
    holding a long-lived session across the poller's lifetime.
    """

    def __init__(self, endpoint: str, token: str) -> None:
        self.endpoint = endpoint
        self._headers = {"Authorization": f"Basic {token}"}
        self._tools: dict[str, str] = {}

    @staticmethod
    def _tool_kind(name: str) -> str:
        """Map either current snake_case or older camelCase names to one operation."""
        folded = "".join(ch for ch in name.lower() if ch.isalnum())
        aliases = {
            "listtopics": "list_topics",
            "getmetadata": "get_metadata",
            "querydata": "query_data",
        }
        return aliases.get(folded, "")

    async def discover_tools(self) -> dict[str, str]:
        """Ask the endpoint for its tool names instead of pinning a naming era."""
        async with streamablehttp_client(self.endpoint, headers=self._headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
        discovered: dict[str, str] = {}
        for tool in getattr(result, "tools", None) or []:
            name = getattr(tool, "name", "")
            kind = self._tool_kind(name)
            if kind:
                discovered[kind] = name
        missing = {"list_topics", "get_metadata", "query_data"} - discovered.keys()
        if missing:
            raise RuntimeError(f"RTCE MCP endpoint is missing expected tool operation(s): {', '.join(sorted(missing))}")
        self._tools = discovered
        return dict(discovered)

    async def _tool_name(self, kind: str) -> str:
        if not self._tools:
            await self.discover_tools()
        return self._tools[kind]

    async def _call(self, name: str, arguments: dict) -> Any:
        async with streamablehttp_client(self.endpoint, headers=self._headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(name=name, arguments=arguments)

    async def query(
        self,
        topic: str,
        where: str = "",
        max_rows: int = MAX_RESULT_ROWS,
        *,
        order_by: str = "",
        limit: int | None = None,
    ) -> list[dict]:
        """Query a topic with a bounded result contract and optional event-time ordering."""
        sql = f'SELECT * FROM "{topic}"'
        if where:
            sql += f" WHERE {where}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit is not None:
            sql += f" LIMIT {max(1, min(limit, MAX_RESULT_ROWS))}"
        result = await self._call(
            await self._tool_name("query_data"),
            {"topic_name": topic, "query": sql, "max_result_rows": min(max_rows, MAX_RESULT_ROWS)},
        )
        return _rows_from_result(result)

    async def list_topics(self) -> Any:
        return await self._call(await self._tool_name("list_topics"), {})

    async def get_metadata(self, topic: str) -> Any:
        return await self._call(await self._tool_name("get_metadata"), {"topic_name": topic})

    @staticmethod
    def topic_names(result: Any) -> list[str]:
        """Extract topic names from list_topics text blocks without logging secrets."""
        names: list[str] = []
        for block in getattr(result, "content", None) or []:
            raw = getattr(block, "text", "")
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            rows = payload if isinstance(payload, list) else payload.get("topics", payload.get("data", []))
            if isinstance(rows, dict):
                rows = rows.get("data", [])
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, str):
                    names.append(row)
                elif isinstance(row, dict):
                    name = row.get("topic_name") or row.get("name") or row.get("topicName")
                    if name:
                        names.append(str(name))
        return sorted(set(names))
