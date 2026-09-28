"""
Tool definitions for the MINAS CN-NSSMF agent.

TOOL_SCHEMAS  — list of tool dicts passed to Ollama (/api/chat)
dispatch_tool — routes a tool_use call to the correct handler

The CN-NSSMF manages 5G Core slice resources. Its 5 tools:
  query_nwdaf    — retrieve predictive analytics from the NWDAF (TS 23.288)
  configure_qos  — apply GBR/MBR/5QI for a slice at PCF (policy) + SMF (session)
  revert_qos     — restore the previous QoS configuration for an intent
  get_core_kpis  — read the latest core telemetry for a slice (core_kpis table)
  record_policy  — amend the policy row configure_qos already wrote (upsert)

configure_qos persists its own policy row: revert_qos can only undo what is in
`policies`, so leaving the write to a separate tool the LLM might skip meant an
applied QoS change could never be reverted.
"""

from __future__ import annotations

from typing import Any

from db import get_db_conn
from guardrails import check as guardrail_check
from nwdaf_client import get_analytics

# Default 5QI per slice type (TS 23.501 Table 5.7.4-1)
#   SST=1 eMBB  -> 5QI 9  (non-GBR, best effort)
#   SST=2 URLLC -> 5QI 82 (delay-critical GBR)
DEFAULT_5QI = {1: 9, 2: 82}

# What the subscribers are provisioned with before MINAS touches anything
# (scripts/provision.js: both sessions use qos.index 9, no GBR) — this is the
# state a revert returns to when no other policy is active for the slice.
BASELINE_QOS = {1: {"qos_5qi": 9, "gbr_dl_mbps": None, "mbr_dl_mbps": None},
                2: {"qos_5qi": 9, "gbr_dl_mbps": None, "mbr_dl_mbps": None}}

# ---------------------------------------------------------------------------
# Tool schemas — Ollama format: {type: "function", function: {name, description, parameters}}
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "query_nwdaf",
            "description": (
                "Retrieve predictive analytics for a slice from the NWDAF via "
                "Nnwdaf_AnalyticsInfo (TS 23.288). Call this before reconfiguring QoS "
                "in a predictive scenario (Use Case 1) to anticipate resource exhaustion."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sst": {"type": "integer", "description": "Target slice SST (1 or 2)"},
                    "analytics_id": {
                        "type": "string",
                        "enum": ["SLICE_LOAD_LEVEL", "NF_LOAD", "USER_DATA_CONGESTION", "ABNORMAL_BEHAVIOUR"],
                        "description": "3GPP analytics identifier (TS 23.288 §6)",
                    },
                    "horizon_seconds": {"type": "integer", "description": "Prediction horizon in seconds (default 60)"},
                },
                "required": ["sst", "analytics_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_qos",
            "description": (
                "Apply QoS parameters for a slice: install/update the PCC rule at the "
                "PCF and the session parameters (Session-AMBR, QoS Flow GBR/MBR) at the "
                "SMF. Returns the applied values and the previous values (for revert)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent_id":   {"type": "integer", "description": "ID of the intent being processed"},
                    "sst":         {"type": "integer", "description": "Target slice SST"},
                    "gbr_dl_mbps": {"type": "number",  "description": "Guaranteed downlink bit rate (Mbps)"},
                    "gbr_ul_mbps": {"type": "number",  "description": "Guaranteed uplink bit rate (Mbps)"},
                    "mbr_dl_mbps": {"type": "number",  "description": "Maximum downlink bit rate (Mbps)"},
                    "mbr_ul_mbps": {"type": "number",  "description": "Maximum uplink bit rate (Mbps)"},
                    "qos_5qi":     {"type": "integer", "description": "5QI value (optional; default per SST)"},
                },
                "required": ["intent_id", "sst", "gbr_dl_mbps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "revert_qos",
            "description": (
                "Restore the QoS configuration that was in place before the policy for "
                "the given intent was applied. Called when a time window expires."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent_id": {"type": "integer", "description": "Intent whose policy must be reverted"},
                },
                "required": ["intent_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_core_kpis",
            "description": "Read the latest core-network KPIs for a slice from the MINAS database (core_kpis table).",
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
            "name": "record_policy",
            "description": (
                "Amend the policy row for an intent (configure_qos already records it). "
                "Only needed to correct the recorded values; calling it again for the same "
                "intent updates that row instead of adding a new one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent_id":         {"type": "integer", "description": "Intent this policy fulfils"},
                    "sst":               {"type": "integer", "description": "Target slice SST"},
                    "enforced_thp_mbps": {"type": "number",  "description": "Throughput actually enforced (Mbps)"},
                    "qos_5qi":           {"type": "integer", "description": "5QI applied"},
                    "gbr_dl_mbps":       {"type": "number",  "description": "GBR downlink applied (Mbps)"},
                    "mbr_dl_mbps":       {"type": "number",  "description": "MBR downlink applied (Mbps)"},
                },
                "required": ["intent_id", "sst", "enforced_thp_mbps"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _query_nwdaf(params: dict) -> dict:
    return get_analytics(
        analytics_id=params["analytics_id"],
        sst=params["sst"],
        horizon_seconds=params.get("horizon_seconds", 60),
    )


def _current_qos(sst: int) -> dict:
    """QoS currently in force for the slice: the newest active policy, or the
    provisioned baseline when none is active.
    TODO: read it live from PCF/SMF once enforcement is real."""
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, intent_id, qos_5qi, gbr_dl_mbps, mbr_dl_mbps
              FROM policies
             WHERE sst = %s AND status = 'active'
          ORDER BY applied_at DESC
             LIMIT 1
            """,
            (sst,),
        )
        row = cur.fetchone()
    if row is None:
        return {"sst": sst, **BASELINE_QOS.get(sst, {}), "source": "provisioned-baseline"}
    return {"sst": sst, "qos_5qi": row[2], "gbr_dl_mbps": row[3], "mbr_dl_mbps": row[4],
            "source": f"policy {row[0]} (intent {row[1]})"}


def _upsert_policy(intent_id: int, sst: int, enforced_thp_mbps: float,
                   qos_5qi: int | None, gbr_dl_mbps: float | None,
                   mbr_dl_mbps: float | None) -> tuple[int, bool]:
    """One active policy row per (intent, slice). Returns (policy_id, created)."""
    conn = get_db_conn()
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE policies
               SET enforced_thp_mbps = %s,
                   qos_5qi     = COALESCE(%s, qos_5qi),
                   gbr_dl_mbps = COALESCE(%s, gbr_dl_mbps),
                   mbr_dl_mbps = COALESCE(%s, mbr_dl_mbps)
             WHERE intent_id = %s AND sst = %s AND status = 'active'
         RETURNING id
            """,
            (enforced_thp_mbps, qos_5qi, gbr_dl_mbps, mbr_dl_mbps, intent_id, sst),
        )
        row = cur.fetchone()
        if row is not None:
            return row[0], False
        cur.execute(
            """
            INSERT INTO policies
                (intent_id, sst, enforced_thp_mbps, qos_5qi, gbr_dl_mbps, mbr_dl_mbps, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'active')
         RETURNING id
            """,
            (intent_id, sst, enforced_thp_mbps, qos_5qi, gbr_dl_mbps, mbr_dl_mbps),
        )
        return cur.fetchone()[0], True


def _configure_qos(params: dict) -> dict:
    sst = params["sst"]
    qos_5qi = params.get("qos_5qi") or DEFAULT_5QI.get(sst)
    gbr_dl = params.get("gbr_dl_mbps")

    previous = _current_qos(sst)

    # TODO: real enforcement. Two normative touch-points:
    #   PCF — install/modify the PCC rule for the S-NSSAI (TS 23.503)
    #   SMF — update Session-AMBR / QoS Flow GBR/MBR for the DNN (TS 23.502)
    # For Open5GS: edit pcf.yaml / smf.yaml policy (or call its API) and reload the NF.
    applied = {
        "sst":         sst,
        "qos_5qi":     qos_5qi,
        "gbr_dl_mbps": gbr_dl,
        "gbr_ul_mbps": params.get("gbr_ul_mbps"),
        "mbr_dl_mbps": params.get("mbr_dl_mbps") or gbr_dl,
        "mbr_ul_mbps": params.get("mbr_ul_mbps"),
    }
    policy_id, _ = _upsert_policy(params["intent_id"], sst, gbr_dl, qos_5qi,
                                  gbr_dl, applied["mbr_dl_mbps"])
    return {"status": "applied", "targets": ["pcf", "smf"], "applied": applied,
            "previous": previous, "policy_id": policy_id}


def _revert_qos(params: dict) -> dict:
    intent_id = params["intent_id"]
    conn = get_db_conn()
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE policies
               SET status = 'reverted', reverted_at = NOW()
             WHERE intent_id = %s AND status = 'active'
         RETURNING id, sst, enforced_thp_mbps
            """,
            (intent_id,),
        )
        rows = cur.fetchall()
    if not rows:
        return {"status": "noop", "reason": f"no active policy for intent {intent_id}"}
    # TODO: push the restored PCC rule / session parameters back to PCF + SMF.
    return {
        "status": "reverted",
        "policy_ids": [r[0] for r in rows],
        "sst": rows[0][1],
        "restored_from_thp_mbps": rows[0][2],
        "now_in_force": _current_qos(rows[0][1]),
    }


def _get_core_kpis(params: dict) -> dict:
    sst = params["sst"]
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT collected_at, ues_registered, pdu_sessions, thp_dl_mbps, thp_ul_mbps
              FROM core_kpis
             WHERE sst = %s
          ORDER BY collected_at DESC
             LIMIT 1
            """,
            (sst,),
        )
        row = cur.fetchone()
    if row is None:
        return {"sst": sst, "kpis": None, "reason": "no telemetry collected yet"}
    return {
        "sst": sst,
        "kpis": {
            "collected_at":   row[0].isoformat(),
            "ues_registered": row[1],
            "pdu_sessions":   row[2],
            "thp_dl_mbps":    row[3],
            "thp_ul_mbps":    row[4],
        },
    }


def _record_policy(params: dict) -> dict:
    policy_id, created = _upsert_policy(
        params["intent_id"], params["sst"], params["enforced_thp_mbps"],
        params.get("qos_5qi"), params.get("gbr_dl_mbps"), params.get("mbr_dl_mbps"),
    )
    return {"policy_id": policy_id, "status": "active", "created": created}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, Any] = {
    "query_nwdaf":   _query_nwdaf,
    "configure_qos": _configure_qos,
    "revert_qos":    _revert_qos,
    "get_core_kpis": _get_core_kpis,
    "record_policy": _record_policy,
}


def dispatch_tool(name: str, params: dict) -> dict:
    err = guardrail_check(name, params, tag="cn-nssmf")
    if err:
        return err
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return handler(params)
    except Exception as exc:
        return {"error": str(exc), "tool": name}
