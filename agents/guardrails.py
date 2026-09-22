"""
agents/guardrails.py
--------------------
Deterministic validation layer for all MINAS tool calls.

validate(tool_name, params) is called by every dispatch_tool() before the
handler runs. It returns None when params are valid, or a structured error
dict when they are not. The LLM receives the error as a tool result and can
correct its parameters on the next ReAct step.

All rules here are pure logic — no DB calls, no LLM, no randomness.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

# RQ3 ablation switch (see TCC-II-DRAFT.tex Section 5, "guardrails on/off"):
# when disabled, validate() is still run so violations are visible in the
# logs, but the error is not returned to the model, so the call proceeds as
# if it had passed validation. Enabled by default — this must be an opt-out,
# not something a benchmark run can silently forget to turn on.
GUARDRAILS_ENABLED = os.getenv("GUARDRAILS_ENABLED", "true").strip().lower() not in ("false", "0", "no")

# Valid SSTs defined by the MINAS deployment (TS 23.501 §5.15.2)
_VALID_SST = {1, 2}

# Valid 5QI range — standardised values (TS 23.501 Table 5.7.4-1)
_5QI_MIN, _5QI_MAX = 1, 86

# Valid intent lifecycle statuses (must match schema.sql CHECK constraint)
_VALID_INTENT_STATUS = {
    "decomposed", "negotiating", "applied", "degraded", "failed", "reverted"
}


def _err(msg: str) -> dict:
    return {"error": msg, "guardrail": True}


def _positive_int(value: Any, field: str) -> dict | None:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return _err(f"{field} must be an integer, got {value!r}")
    if v <= 0:
        return _err(f"{field} must be a positive integer, got {v}")
    return None


def _positive_float(value: Any, field: str) -> dict | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return _err(f"{field} must be a number, got {value!r}")
    if v <= 0:
        return _err(f"{field} must be > 0, got {v}")
    return None


def _valid_sst(value: Any) -> dict | None:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return _err(f"sst must be an integer, got {value!r}")
    if v not in _VALID_SST:
        return _err(f"sst must be one of {sorted(_VALID_SST)}, got {v}")
    return None


def _parse_iso(value: str, field: str) -> tuple[datetime | None, dict | None]:
    if not isinstance(value, str):
        return None, _err(f"{field} must be an ISO-8601 string, got {value!r}")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt, None
    except ValueError:
        return None, _err(f"{field} is not a valid ISO-8601 timestamp: {value!r}")


# ---------------------------------------------------------------------------
# Per-tool validators
# ---------------------------------------------------------------------------

def _validate_record_intent(p: dict) -> dict | None:
    if (e := _valid_sst(p.get("sst"))):
        return e

    thp = p.get("target_thp_mbps")
    if thp is not None:
        if (e := _positive_float(thp, "target_thp_mbps")):
            return e

    ws = p.get("window_start")
    we = p.get("window_end")
    if ws is not None or we is not None:
        if ws is None:
            return _err("window_start is required when window_end is provided")
        if we is None:
            return _err("window_end is required when window_start is provided")
        dt_start, err = _parse_iso(ws, "window_start")
        if err:
            return err
        dt_end, err = _parse_iso(we, "window_end")
        if err:
            return err
        if dt_end <= dt_start:
            return _err(
                f"window_end ({we}) must be after window_start ({ws})"
            )

    return None


def _validate_update_intent_status(p: dict) -> dict | None:
    if (e := _positive_int(p.get("intent_id"), "intent_id")):
        return e
    status = p.get("status")
    if status not in _VALID_INTENT_STATUS:
        return _err(
            f"status must be one of {sorted(_VALID_INTENT_STATUS)}, got {status!r}"
        )
    return None


def _validate_configure_qos(p: dict) -> dict | None:
    if (e := _positive_int(p.get("intent_id"), "intent_id")):
        return e
    if (e := _valid_sst(p.get("sst"))):
        return e
    if (e := _positive_float(p.get("gbr_dl_mbps"), "gbr_dl_mbps")):
        return e

    gbr_dl = float(p["gbr_dl_mbps"])

    gbr_ul = p.get("gbr_ul_mbps")
    if gbr_ul is not None:
        if (e := _positive_float(gbr_ul, "gbr_ul_mbps")):
            return e

    mbr_dl = p.get("mbr_dl_mbps")
    if mbr_dl is not None:
        if (e := _positive_float(mbr_dl, "mbr_dl_mbps")):
            return e
        if float(mbr_dl) < gbr_dl:
            return _err(
                f"mbr_dl_mbps ({mbr_dl}) must be >= gbr_dl_mbps ({gbr_dl})"
            )

    mbr_ul = p.get("mbr_ul_mbps")
    if mbr_ul is not None:
        if (e := _positive_float(mbr_ul, "mbr_ul_mbps")):
            return e
        if gbr_ul is not None and float(mbr_ul) < float(gbr_ul):
            return _err(
                f"mbr_ul_mbps ({mbr_ul}) must be >= gbr_ul_mbps ({gbr_ul})"
            )

    qos_5qi = p.get("qos_5qi")
    if qos_5qi is not None:
        try:
            v = int(qos_5qi)
        except (TypeError, ValueError):
            return _err(f"qos_5qi must be an integer, got {qos_5qi!r}")
        if not (_5QI_MIN <= v <= _5QI_MAX):
            return _err(
                f"qos_5qi must be in [{_5QI_MIN}, {_5QI_MAX}] (TS 23.501), got {v}"
            )

    return None


def _validate_intent_id_only(p: dict) -> dict | None:
    return _positive_int(p.get("intent_id"), "intent_id")


def _validate_allocate_prb(p: dict) -> dict | None:
    if (e := _positive_int(p.get("intent_id"), "intent_id")):
        return e
    if (e := _valid_sst(p.get("sst"))):
        return e
    if (e := _positive_float(p.get("target_thp_mbps"), "target_thp_mbps")):
        return e
    return None


def _validate_estimate_capacity(p: dict) -> dict | None:
    if (e := _valid_sst(p.get("sst"))):
        return e
    if (e := _positive_float(p.get("target_thp_mbps"), "target_thp_mbps")):
        return e
    return None


def _validate_query_nwdaf(p: dict) -> dict | None:
    if (e := _valid_sst(p.get("sst"))):
        return e
    horizon = p.get("horizon_seconds")
    if horizon is not None:
        try:
            v = int(horizon)
        except (TypeError, ValueError):
            return _err(f"horizon_seconds must be an integer, got {horizon!r}")
        if v <= 0:
            return _err(f"horizon_seconds must be > 0, got {v}")
    return None


def _validate_record_policy(p: dict) -> dict | None:
    if (e := _positive_int(p.get("intent_id"), "intent_id")):
        return e
    if (e := _valid_sst(p.get("sst"))):
        return e
    if (e := _positive_float(p.get("enforced_thp_mbps"), "enforced_thp_mbps")):
        return e
    return None


def _validate_get_sst_only(p: dict) -> dict | None:
    return _valid_sst(p.get("sst"))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_VALIDATORS: dict[str, Any] = {
    # Orchestrator
    "record_intent":        _validate_record_intent,
    "update_intent_status": _validate_update_intent_status,
    # CN-NSSMF
    "configure_qos":  _validate_configure_qos,
    "revert_qos":     _validate_intent_id_only,
    "get_core_kpis":  _validate_get_sst_only,
    "record_policy":  _validate_record_policy,
    "query_nwdaf":    _validate_query_nwdaf,
    # RAN-NSSMF
    "allocate_prb":      _validate_allocate_prb,
    "revert_prb":        _validate_intent_id_only,
    "get_ran_kpis":      _validate_get_sst_only,
    "get_slice_load":    _validate_get_sst_only,
    "estimate_capacity": _validate_estimate_capacity,
}


def validate(tool_name: str, params: dict) -> dict | None:
    """
    Validate params for tool_name before execution.

    Returns None if valid, or {"error": str, "guardrail": True} if not.
    Tools without a registered validator pass through unconditionally.
    """
    fn = _VALIDATORS.get(tool_name)
    if fn is None:
        return None
    return fn(params)


def check(tool_name: str, params: dict, tag: str = "guardrails") -> dict | None:
    """Entry point for dispatch_tool(): runs validate() and applies the
    GUARDRAILS_ENABLED ablation switch. Returns the same shape as validate()
    — None when the call may proceed, or the structured error dict when it
    must be blocked."""
    err = validate(tool_name, params)
    if err is None:
        return None
    if GUARDRAILS_ENABLED:
        return err
    print(f"[{tag}] guardrails disabled (ablation) — would have rejected "
          f"{tool_name}({params}): {err['error']}")
    return None
