"""
Tool definitions for the MINAS RAN-NSSMF agent.

TOOL_SCHEMAS  — list of tool dicts passed to Ollama (/api/chat)
dispatch_tool — routes a tool_use call to the correct handler

The RAN-NSSMF manages radio access resources per slice. Its 5 tools:
  get_ran_kpis      — latest RAN telemetry for a slice (ran_kpis table)
  get_slice_load    — load index for a slice; computes + persists it if stale
  estimate_capacity — PRBs needed for a throughput target and whether it fits
  allocate_prb      — set the PRB / resource share for a slice at the gNB
  revert_prb        — restore the previous allocation for an intent
"""

from __future__ import annotations

import math
import os
from typing import Any

from db import get_db_conn
from guardrails import validate

# Rough radio model — replace with real link adaptation from ran_kpis (MCS -> SE).
PRB_TOTAL     = int(os.getenv("RAN_PRB_TOTAL", "51"))        # ~20 MHz @ 30 kHz SCS
MBPS_PER_PRB  = float(os.getenv("RAN_MBPS_PER_PRB", "0.40")) # single layer, mid MCS
RECENT_WINDOW = "30 seconds"

# ---------------------------------------------------------------------------
# Tool schemas — Ollama format: {type: "function", function: {name, description, parameters}}
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_ran_kpis",
            "description": "Read the most recent RAN KPIs for a slice (RSRP, SINR, MCS, PRB usage, throughput).",
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
            "name": "get_slice_load",
            "description": (
                "Return the load index for a slice. If no recent value exists it is "
                "computed from ran_kpis and persisted (slice_load table)."
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
            "name": "estimate_capacity",
            "description": (
                "Estimate the PRBs needed to guarantee a throughput target on a slice "
                "and whether the current PRB budget can fit it. Use before allocate_prb; "
                "if it does not fit, report the shortfall to the Orchestrator."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sst":             {"type": "integer", "description": "Target slice SST"},
                    "target_thp_mbps": {"type": "number",  "description": "Requested guaranteed throughput (Mbps)"},
                },
                "required": ["sst", "target_thp_mbps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "allocate_prb",
            "description": (
                "Set the PRB / resource share for a slice at the gNB. Returns the "
                "allocated and previous values. Status is 'degraded' if the budget "
                "could not cover the full target."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent_id":       {"type": "integer", "description": "ID of the intent being processed"},
                    "sst":             {"type": "integer", "description": "Target slice SST"},
                    "target_thp_mbps": {"type": "number",  "description": "Throughput the allocation must support (Mbps)"},
                },
                "required": ["intent_id", "sst", "target_thp_mbps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "revert_prb",
            "description": "Restore the PRB allocation that was in place before the intent was applied.",
            "parameters": {
                "type": "object",
                "properties": {
                    "intent_id": {"type": "integer", "description": "Intent whose allocation must be reverted"},
                },
                "required": ["intent_id"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _recent_ran(sst: int | None) -> dict:
    """Aggregate the last ~30 s of ran_kpis. sst=None -> all slices."""
    conn = get_db_conn()
    where = "collected_at > NOW() - INTERVAL %s"
    params: list[Any] = [RECENT_WINDOW]
    if sst is not None:
        where += " AND sst = %s"
        params.append(sst)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT COUNT(DISTINCT ue_id), AVG(rsrp_dbm), AVG(sinr_db), AVG(mcs_dl),
                   COALESCE(SUM(prb_used_dl), 0), COALESCE(SUM(thp_dl_mbps), 0),
                   COALESCE(SUM(thp_ul_mbps), 0)
              FROM ran_kpis
             WHERE {where}
            """,
            params,
        )
        row = cur.fetchone()
    return {
        "ues": row[0] or 0,
        "rsrp_dbm": float(row[1]) if row[1] is not None else None,
        "sinr_db": float(row[2]) if row[2] is not None else None,
        "mcs_dl": float(row[3]) if row[3] is not None else None,
        "prb_used_dl": int(row[4]),
        "thp_dl_mbps": round(float(row[5]), 3),
        "thp_ul_mbps": round(float(row[6]), 3),
    }


def _current_allocation(sst: int) -> dict:
    # TODO: read the live per-slice PRB share from the gNB (srsRAN sched config / RIC).
    return {"sst": sst, "prb": None, "source": "mock"}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _get_ran_kpis(params: dict) -> dict:
    sst = params["sst"]
    agg = _recent_ran(sst)
    if agg["ues"] == 0:
        return {"sst": sst, "kpis": None, "reason": "no RAN telemetry collected yet"}
    return {"sst": sst, "kpis": agg}


def _get_slice_load(params: dict) -> dict:
    sst = params["sst"]
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT computed_at, load_index, ues_ativos, ues_total, prb_used_dl, sinr_avg_db, thp_dl_mbps
              FROM slice_load
             WHERE sst = %s AND computed_at > NOW() - INTERVAL %s
          ORDER BY computed_at DESC
             LIMIT 1
            """,
            (sst, RECENT_WINDOW),
        )
        row = cur.fetchone()
    if row is not None:
        return {
            "sst": sst,
            "computed_at": row[0].isoformat(),
            "load_index": float(row[1]),
            "ues_ativos": row[2],
            "ues_total": row[3],
            "prb_used_dl": row[4],
            "sinr_avg_db": float(row[5]) if row[5] is not None else None,
            "thp_dl_mbps": float(row[6]),
            "source": "cached",
        }

    # stale/missing -> compute from ran_kpis and persist (RAN-NSSMF writes slice_load)
    slice_agg = _recent_ran(sst)
    all_agg = _recent_ran(None)
    if slice_agg["ues"] == 0:
        return {"sst": sst, "load_index": None, "reason": "no RAN telemetry to compute from"}

    ues_total = max(all_agg["ues"], 1)
    # carga_slice = (ues_ativos / ues_total) * throughput_atual  (schema.sql)
    load_index = round((slice_agg["ues"] / ues_total) * slice_agg["thp_dl_mbps"], 3)

    with conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO slice_load
                (sst, ues_ativos, ues_total, thp_dl_mbps, load_index, prb_used_dl, sinr_avg_db)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (sst, slice_agg["ues"], ues_total, slice_agg["thp_dl_mbps"],
             load_index, slice_agg["prb_used_dl"], slice_agg["sinr_db"]),
        )
    return {
        "sst": sst,
        "load_index": load_index,
        "ues_ativos": slice_agg["ues"],
        "ues_total": ues_total,
        "prb_used_dl": slice_agg["prb_used_dl"],
        "sinr_avg_db": slice_agg["sinr_db"],
        "thp_dl_mbps": slice_agg["thp_dl_mbps"],
        "source": "computed",
    }


def _estimate_capacity(params: dict) -> dict:
    sst = params["sst"]
    target = params["target_thp_mbps"]

    prb_needed = math.ceil(target / MBPS_PER_PRB)
    prb_used_other = _recent_ran(None)["prb_used_dl"] - _recent_ran(sst)["prb_used_dl"]
    prb_available = max(PRB_TOTAL - max(prb_used_other, 0), 0)
    feasible = prb_needed <= prb_available
    shortfall = 0.0 if feasible else round((prb_needed - prb_available) * MBPS_PER_PRB, 2)

    return {
        "sst": sst,
        "target_thp_mbps": target,
        "prb_needed": prb_needed,
        "prb_available": prb_available,
        "prb_total": PRB_TOTAL,
        "feasible": feasible,
        "shortfall_mbps": shortfall,
        "model": "static",  # TODO: link adaptation from ran_kpis MCS
    }


def _allocate_prb(params: dict) -> dict:
    sst = params["sst"]
    intent_id = params["intent_id"]
    est = _estimate_capacity({"sst": sst, "target_thp_mbps": params["target_thp_mbps"]})
    previous = _current_allocation(sst)

    prb_alloc = est["prb_needed"] if est["feasible"] else est["prb_available"]
    status = "applied" if est["feasible"] else "degraded"

    # TODO: real enforcement at the gNB. srsRAN has no dynamic per-slice PRB via
    # config reload; this is where a RIC/xApp (E2) or a scheduler policy would act.
    print(f"[ran-nssmf] allocate_prb sst={sst} prb={prb_alloc} status={status}")

    conn = get_db_conn()
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ran_allocations
                (intent_id, sst, prb_allocated, supported_thp_mbps, prb_previous, status)
            VALUES (%s, %s, %s, %s, %s, 'active')
            """,
            (
                intent_id,
                sst,
                prb_alloc,
                round(prb_alloc * MBPS_PER_PRB, 2),
                previous.get("prb"),
            ),
        )

    return {
        "status": status,
        "targets": ["srsran-gnb"],
        "sst": sst,
        "prb_allocated": prb_alloc,
        "supported_thp_mbps": round(prb_alloc * MBPS_PER_PRB, 2),
        "shortfall_mbps": est["shortfall_mbps"],
        "previous": previous,
    }


def _revert_prb(params: dict) -> dict:
    intent_id = params["intent_id"]
    conn = get_db_conn()
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ran_allocations
               SET status = 'reverted', reverted_at = NOW()
             WHERE intent_id = %s AND status = 'active'
         RETURNING id, sst, prb_allocated, prb_previous
            """,
            (intent_id,),
        )
        row = cur.fetchone()

    if row is None:
        return {"status": "noop", "reason": f"no active allocation for intent {intent_id}"}

    alloc_id, sst, prb_was, prb_restore = row
    print(f"[ran-nssmf] revert_prb intent={intent_id} sst={sst} "
          f"from={prb_was} to={prb_restore}")

    # TODO: push restored PRB share to gNB (RIC/xApp E2) when prb_restore is known.
    return {
        "status": "reverted",
        "allocation_id": alloc_id,
        "sst": sst,
        "prb_was": prb_was,
        "prb_restored": prb_restore,
        "note": "gNB enforcement pending RIC/E2 integration",
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, Any] = {
    "get_ran_kpis":      _get_ran_kpis,
    "get_slice_load":    _get_slice_load,
    "estimate_capacity": _estimate_capacity,
    "allocate_prb":      _allocate_prb,
    "revert_prb":        _revert_prb,
}


def dispatch_tool(name: str, params: dict) -> dict:
    err = validate(name, params)
    if err:
        return err
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return handler(params)
    except Exception as exc:
        return {"error": str(exc), "tool": name}
