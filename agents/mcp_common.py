"""
Shared MCP (Model Context Protocol) client helpers for MINAS.

The Orchestrator — and its reversion scheduler — reach the domain agents
CN-NSSMF and RAN-NSSMF over MCP instead of plain HTTP. Each domain agent
runs a FastMCP server (streamable-HTTP transport) that exposes its
directive actions as MCP tools; here we discover and invoke them.

Both public helpers are synchronous wrappers (asyncio.run) so they drop
into the existing synchronous ReAct loop and the scheduler thread with no
other change.

  list_remote_tools(base_url) -> [tool schema, ...]   # MCP tools/list
  call_remote_tool(base_url, name, arguments) -> dict  # MCP tools/call

Reference: MCP spec (modelcontextprotocol.io); coordination "mediated by
the Orchestrator" from the MINAS architecture (TCC I cap. 4). NWDAF is not
here on purpose: it is reached over HTTP because it stands in for a 3GPP
Service-Based Interface (Nnwdaf_AnalyticsInfo, TS 23.288 / TS 29.520).
"""

from __future__ import annotations

import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# FastMCP mounts the streamable-HTTP endpoint at /mcp by default.
MCP_PATH = "/mcp"


def _endpoint(base_url: str) -> str:
    return base_url.rstrip("/") + MCP_PATH


def _result_to_dict(result) -> dict:
    """Normalise an MCP CallToolResult into a plain JSON-able dict."""
    if getattr(result, "isError", False):
        texts = [c.text for c in getattr(result, "content", [])
                 if getattr(c, "type", None) == "text"]
        return {"error": "\n".join(texts).strip() or "tool call returned an error"}

    texts = [c.text for c in getattr(result, "content", [])
             if getattr(c, "type", None) == "text"]
    raw = "\n".join(texts).strip()
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"result": raw}

    sc = getattr(result, "structuredContent", None)
    if isinstance(sc, dict):
        return sc.get("result", sc) if "result" in sc else sc
    if sc is not None:
        return {"result": sc}
    return {"status": "ok"}


async def _alist_tools(base_url: str) -> list[dict]:
    async with streamablehttp_client(_endpoint(base_url)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema or {"type": "object", "properties": {}},
            },
        }
        for t in listed.tools
    ]


async def _acall_tool(base_url: str, name: str, arguments: dict) -> dict:
    async with streamablehttp_client(_endpoint(base_url)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
    return _result_to_dict(result)


def list_remote_tools(base_url: str) -> list[dict]:
    """Discover a domain agent's MCP tools as Ollama-format tool schemas.
    Raises on connection failure so the caller can decide how to degrade
    (e.g. retry later — the server may still be starting up)."""
    return asyncio.run(_alist_tools(base_url))


def call_remote_tool(base_url: str, name: str, arguments: dict | None = None) -> dict:
    """Invoke one MCP tool on a domain agent. Returns a JSON-able dict and
    never raises — transport or tool errors come back as {"error": ...}."""
    try:
        return asyncio.run(_acall_tool(base_url, name, arguments or {}))
    except Exception as exc:  # noqa: BLE001 — surface any transport/tool error
        return {"error": str(exc), "mcp_target": base_url, "tool": name}
