"""MCP client for the Confluent Real-Time Context Engine (RTCE).

RTCE serves materialized topic data to AI agents over an MCP endpoint:

    https://mcp.<REGION>.aws.confluent.cloud/mcp/v1/context-engine
        /organizations/<ORG_ID>/environments/<ENV_ID>/kafka-clusters/<LKC_ID>

Auth is HTTP Basic with a **Global** Confluent Cloud API key:
``Authorization: Basic base64(<KEY>:<SECRET>)``. Once connected, RTCE exposes
three MCP tools — ``query_data`` (SQL-ish ``SELECT … WHERE …`` over a topic),
``list_topics``, and ``get_metadata``.

This wrapper speaks the streamable-HTTP MCP transport via the official ``mcp``
SDK and parses ``query_data`` results into plain row dicts. It is deliberately
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


def basic_token(api_key: str, api_secret: str) -> str:
    """Base64 ``key:secret`` for the RTCE ``Authorization: Basic`` header."""
    return base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()


def build_endpoint(region: str, org_id: str, env_id: str, cluster_id: str) -> str:
    """Construct the RTCE MCP endpoint URL from its component IDs."""
    return (
        f"https://mcp.{region}.aws.confluent.cloud/mcp/v1/context-engine"
        f"/organizations/{org_id}/environments/{env_id}/kafka-clusters/{cluster_id}"
    )


def _coerce_rows(data: Any) -> list[dict]:
    """Pull a list of row dicts out of whatever shape query_data returned."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("rows", "data", "results", "records", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
        return [data]  # a single row object
    return []


def _rows_from_result(result: Any) -> list[dict]:
    """Extract row dicts from an MCP ``CallToolResult`` (JSON in text blocks)."""
    rows: list[dict] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            rows.extend(_coerce_rows(json.loads(text)))
        except json.JSONDecodeError:
            logger.debug("non-JSON content block from RTCE: %.120s", text)
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

    async def _call(self, name: str, arguments: dict) -> Any:
        async with streamablehttp_client(self.endpoint, headers=self._headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(name=name, arguments=arguments)

    async def query(self, topic: str, query: str = "SELECT *") -> list[dict]:
        """Run ``query_data`` against ``topic`` and return parsed rows."""
        result = await self._call("query_data", {"topic_name": topic, "query": query})
        return _rows_from_result(result)

    async def list_topics(self) -> Any:
        return await self._call("list_topics", {})

    async def get_metadata(self, topic: str) -> Any:
        return await self._call("get_metadata", {"topic_name": topic})
