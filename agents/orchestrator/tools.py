"""
Tool definitions for the MINAS Orchestrator.

get_tool_schemas() — local tools + domain-agent tools discovered over MCP
dispatch_tool      — routes a tool call to the local handler or the right
                     domain-agent MCP server

Local tools (PostgreSQL only, no coordination):
  record_intent        — persist intent to the intents table
  get_sla_status       — read latest KPIs for a slice
  update_intent_status — update intent lifecycle

Domain-agent tools are NOT hard-coded here: at first use the Orchestrator
connects to the CN-NSSMF and RAN-NSSMF MCP servers, lists their tools and
merges them into the schema list with a `cn_nssmf_` / `ran_nssmf_` prefix
(so the two `check_sla` tools don't collide). This is the "coordination
mediated by the Orchestrator via MCP" from the MINAS architecture.
"""

from __future__ import annotations

import os
from typing import Any

from db import get_db_conn
from mcp_common import call_remote_tool, list_remote_tools

# Domain-agent MCP servers the Orchestrator coordinates (overridable via env)
CN_NSSMF_URL  = os.getenv("CN_NSSMF_URL",  "http://cn-nssmf:8001")
RAN_NSSMF_URL = os.getenv("RAN_NSSMF_URL", "http://ran-nssmf:8002")

_DOMAIN_AGENTS: dict[str, str] = {
    "cn_nssmf":  CN_NSSMF_URL,
    "ran_nssmf": RAN_NSSMF_URL,
}

# ---------------------------------------------------------------------------
# Local tool schemas — Ollama format: {type:"function", function:{...}}
# ---------------------------------------------------------------------------

LOCAL_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "record_intent",
            "description": (
                "Persist a decoded operator intent to the database before any action is taken. "
                "Must be called as the first step after receiving a new intent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "raw_text":        {"type": "string",  "description": "Original natural-language intent"},
                    "sst":             {"type": "integer", "description": "Target slice SST (1 or 2)"},
                    "target_thp_mbps": {"type": "number",  "description": "Requested throughput guarantee in Mbps"},
                    "window_start":    {"type": "string",  "description": "ISO-8601 start of enforcement window (optional)"},
                    "window_end":      {"type": "string",  "description": "ISO-8601 end of enforcement window (optional)"},
                },
                "required": ["raw_text", "sst"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sla_status",
            "description": (
                "Read the latest KPIs and SLA compliance state for a given slice "
                "from the MINAS database (core_kpis + slice_load tables)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sst": {"type": "integer", "description": "Slice SST to query (1 or 2)"},
                },
                "required": ["sst"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_intent_status",
            "description": "Update the lifecycle status of an intent in the database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "intent_id": {"type": "integer", "description": "Intent ID to update"},
                    "status": {
                        "type": "string",
                        "enum": ["decomposed", "negotiating", "applied", "degraded", "failed", "reverted"],
                    },
                },
                "required": ["intent_id", "status"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# MCP discovery of domain-agent tools (lazy, cached, retried until complete)
# ---------------------------------------------------------------------------

_remote_schemas: list[dict] = []
_remote_routing: dict[str, tuple[str, str]] = {}   # prefixed name -> (base_url, real name)
_discovered: set[str] = set()


def _discover_remote() -> None:
    """Connect to any not-yet-discovered domain-agent MCP server, list its
    tools and merge them (prefixed). Safe to call repeatedly — a server that
    is still starting up is simply retried next time."""
    global _remote_schemas, _remote_routing

    schemas = list(_remote_schemas)
    routing = dict(_remote_routing)

    for prefix, url in _DOMAIN_AGENTS.items():
        if prefix in _discovered:
            continue
        try:
            fresh: list[dict] = []
            for tool in list_remote_tools(url):
                real = tool["function"]["name"]
                pref = f"{prefix}_{real}"
                fn = {
                    **tool["function"],
                    "name": pref,
                    "description": f"[{prefix.replace('_', '-')}] "
                                   + (tool["function"].get("description") or ""),
                }
                fresh.append({"type": "function", "function": fn})
                routing[pref] = (url, real)
            # replace any stale entries for this prefix
            schemas = [s for s in schemas
                       if not s["function"]["name"].startswith(f"{prefix}_")] + fresh
            _discovered.add(prefix)
            print(f"[orchestrator] MCP tools from {url}: "
                  f"{[r for r in routing if r.startswith(prefix + '_')]}")
        except Exception as exc:  # noqa: BLE001 — server not ready yet, retry later
            print(f"[orchestrator] MCP discovery pending for {url}: {exc}")

    _remote_schemas, _remote_routing = schemas, routing


def get_tool_schemas() -> list[dict]:
    """Local tools + domain-agent tools discovered over MCP. Discovery is
    retried on every call until all domain agents have answered once."""
    if len(_discovered) < len(_DOMAIN_AGENTS):
        _discover_remote()
    return LOCAL_TOOL_SCHEMAS + _remote_schemas

# ---------------------------------------------------------------------------
# Local handlers
# ---------------------------------------------------------------------------

def _clean(value: Any) -> Any:
    """LLMs sometimes send the literal string "null"/"none"/"" for an optional
    field instead of omitting it. Treat those as a missing value."""
    if isinstance(value, str) and value.strip().lower() in ("", "null", "none", "n/a"):
        return None
    return value


def _record_intent(params: dict) -> dict:
    conn = get_db_conn()
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO intents (raw_text, sst, target_thp_mbps, window_start, window_end, status)
            VALUES (%s, %s, %s, %s, %s, 'received')
            RETURNING id
            """,
            (
                params["raw_text"],
                int(params["sst"]),
                _clean(params.get("target_thp_mbps")),
                _clean(params.get("window_start")),
                _clean(params.get("window_end")),
            ),
        )
        intent_id = cur.fetchone()[0]
    return {"intent_id": intent_id, "status": "received"}


def _get_sla_status(params: dict) -> dict:
    sst = params["sst"]
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT thp_dl_mbps, thp_ul_mbps, ues_registered, pdu_sessions
            FROM core_kpis
            WHERE sst = %s
            ORDER BY collected_at DESC
            LIMIT 1
            """,
            (sst,),
        )
        core = cur.fetchone()

        cur.execute(
            """
            SELECT load_index, prb_used_dl, sinr_avg_db
            FROM slice_load
            WHERE sst = %s
            ORDER BY computed_at DESC
            LIMIT 1
            """,
            (sst,),
        )
        load = cur.fetchone()

    return {
        "sst": sst,
        "core": {
            "thp_dl_mbps":    core[0] if core else None,
            "thp_ul_mbps":    core[1] if core else None,
            "ues_registered": core[2] if core else None,
            "pdu_sessions":   core[3] if core else None,
        },
        "ran": {
            "load_index":  load[0] if load else None,
            "prb_used_dl": load[1] if load else None,
            "sinr_avg_db": load[2] if load else None,
        },
    }


def _update_intent_status(params: dict) -> dict:
    conn = get_db_conn()
    with conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE intents SET status = %s WHERE id = %s",
            (params["status"], params["intent_id"]),
        )
    return {"intent_id": params["intent_id"], "status": params["status"]}


_LOCAL_HANDLERS: dict[str, Any] = {
    "record_intent":        _record_intent,
    "get_sla_status":       _get_sla_status,
    "update_intent_status": _update_intent_status,
}

# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def dispatch_tool(name: str, params: dict) -> dict:
    if name in _LOCAL_HANDLERS:
        try:
            return _LOCAL_HANDLERS[name](params)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "tool": name}

    if name not in _remote_routing:
        get_tool_schemas()  # maybe the agent wasn't up during the first pass

    if name in _remote_routing:
        base_url, real = _remote_routing[name]
        return call_remote_tool(base_url, real, params)

    return {"error": f"unknown tool: {name}"}
