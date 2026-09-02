"""
Tool definitions for the MINAS Orchestrator.

TOOL_SCHEMAS  — list of tool dicts passed to the Anthropic API
dispatch_tool — routes a tool_use call to the correct handler

The orchestrator has 5 tools:
  record_intent       — persist intent to PostgreSQL (intents table)
  get_sla_status      — read latest KPIs for a slice from PostgreSQL
  invoke_cn_nssmf     — send a directive to the CN-NSSMF agent
  invoke_ran_nssmf    — send a directive to the RAN-NSSMF agent
  update_intent_status — update intent lifecycle in the database
"""

from __future__ import annotations

import os
from typing import Any

import requests

from db import get_db_conn

# Base URLs for domain agents (overridable via env)
CN_NSSMF_URL  = os.getenv("CN_NSSMF_URL",  "http://cn-nssmf:8001")
RAN_NSSMF_URL = os.getenv("RAN_NSSMF_URL", "http://ran-nssmf:8002")

# ---------------------------------------------------------------------------
# Tool schemas (passed verbatim to anthropic.messages.create)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "record_intent",
        "description": (
            "Persist a decoded operator intent to the database before any action is taken. "
            "Must be called as the first step after receiving a new intent."
        ),
        "input_schema": {
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
    {
        "name": "get_sla_status",
        "description": (
            "Read the latest KPIs and SLA compliance state for a given slice "
            "from the MINAS database (core_kpis + slice_load tables)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sst": {"type": "integer", "description": "Slice SST to query (1 or 2)"},
            },
            "required": ["sst"],
        },
    },
    {
        "name": "invoke_cn_nssmf",
        "description": (
            "Send a directive to the CN-NSSMF agent. "
            "Use for QoS reconfiguration (GBR/MBR), NWDAF queries, or policy revert."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "intent_id": {"type": "integer", "description": "ID of the intent being processed"},
                "action": {
                    "type": "string",
                    "enum": ["apply_qos", "revert_qos", "query_nwdaf", "check_sla"],
                    "description": "Action to perform",
                },
                "sst":             {"type": "integer", "description": "Target slice SST"},
                "target_thp_mbps": {"type": "number",  "description": "Required throughput in Mbps (for apply_qos)"},
                "window_end":      {"type": "string",  "description": "ISO-8601 end of window (for apply_qos with time constraint)"},
            },
            "required": ["intent_id", "action", "sst"],
        },
    },
    {
        "name": "invoke_ran_nssmf",
        "description": (
            "Send a directive to the RAN-NSSMF agent. "
            "Use for PRB allocation adjustment or revert."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "intent_id": {"type": "integer", "description": "ID of the intent being processed"},
                "action": {
                    "type": "string",
                    "enum": ["apply_resources", "revert_resources", "check_sla"],
                    "description": "Action to perform",
                },
                "sst":             {"type": "integer", "description": "Target slice SST"},
                "target_thp_mbps": {"type": "number",  "description": "Required throughput in Mbps (for apply_resources)"},
            },
            "required": ["intent_id", "action", "sst"],
        },
    },
    {
        "name": "update_intent_status",
        "description": "Update the lifecycle status of an intent in the database.",
        "input_schema": {
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
]

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

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
                params["sst"],
                params.get("target_thp_mbps"),
                params.get("window_start"),
                params.get("window_end"),
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


def _invoke_cn_nssmf(params: dict) -> dict:
    try:
        resp = requests.post(
            f"{CN_NSSMF_URL}/directive",
            json=params,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        return {"error": str(exc), "agent": "cn-nssmf"}


def _invoke_ran_nssmf(params: dict) -> dict:
    try:
        resp = requests.post(
            f"{RAN_NSSMF_URL}/directive",
            json=params,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        return {"error": str(exc), "agent": "ran-nssmf"}


def _update_intent_status(params: dict) -> dict:
    conn = get_db_conn()
    with conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE intents SET status = %s WHERE id = %s",
            (params["status"], params["intent_id"]),
        )
    return {"intent_id": params["intent_id"], "status": params["status"]}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, Any] = {
    "record_intent":        _record_intent,
    "get_sla_status":       _get_sla_status,
    "invoke_cn_nssmf":      _invoke_cn_nssmf,
    "invoke_ran_nssmf":     _invoke_ran_nssmf,
    "update_intent_status": _update_intent_status,
}


def dispatch_tool(name: str, params: dict) -> dict:
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return handler(params)
    except Exception as exc:
        return {"error": str(exc), "tool": name}
