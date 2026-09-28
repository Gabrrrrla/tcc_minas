"""
MINAS Orchestrator Agent
Receives operator intents in natural language, decomposes them into
directives, and coordinates CN-NSSMF and RAN-NSSMF via tool-calling.

Paradigm  : ReAct (Reasoning and Acting) — no fine-tuning
Domain    : injected via system prompt + tool descriptions
Backend   : Ollama (local inference server) — see agents/react.py
Transport : HTTP POST /intent  (operator / tests)   +   CLI  (python main.py "<intent>")
"""

import json
import math
import os
import sys
import threading

# make agents/ importable (shared db.py + react.py) whether run via Docker or `cd agents/orchestrator`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request

import scheduler
import sla_monitor
import timeutil
from db import get_db_conn
from rag.retriever import format_context, retrieve
from react import MODEL, OLLAMA_URL, react_loop
from tools import dispatch_tool, get_tool_schemas, intent_status

PORT = int(os.getenv("ORCHESTRATOR_PORT", "8000"))

# RQ3 ablation switch (see TCC-II-DRAFT.tex Section 5, "RAG on/off"): when
# disabled, the intent goes straight to the ReAct loop with no retrieved
# context, isolating RAG's contribution from the guardrail layer's.
RAG_ENABLED = os.getenv("RAG_ENABLED", "true").strip().lower() not in ("false", "0", "no")

SYSTEM_PROMPT = """You are the MINAS Orchestrator, the single coordination point of a
Multi-Agent System for autonomous 5G network slice management.

## Role
- Receive operator intents expressed in natural language.
- Interpret the intent and extract the target slice (SST), QoS objective,
  and optional enforcement window (window_start / window_end).
- Decompose the intent into directives and call the domain agents. Their
  actions reach you as MCP tools, prefixed by agent:
    * cn_nssmf_*  — CN-NSSMF: 5G Core (AMF, SMF, PCF, UPF, NWDAF)
                    e.g. cn_nssmf_apply_qos, cn_nssmf_query_nwdaf
    * ran_nssmf_* — RAN-NSSMF: Radio Access Network (srsRAN gNB)
                    e.g. ran_nssmf_apply_resources
- Monitor SLA compliance and trigger reversions when a time window expires.
- Never access core or RAN interfaces directly; your role is purely semantic.

## Slices in operation
- SST=1 (eMBB)  — streaming / scheduled QoS (Use Case 2)
- SST=2 (URLLC) — mission-critical / predictive resource adjustment (Use Case 1)

## 3GPP reference vocabulary (TS 28.312 / TS 28.552)
- S-NSSAI: {sst, sd} identifying a network slice
- GBR (Guaranteed Bit Rate), MBR (Maximum Bit Rate) — QoS parameters
- 5QI: QoS Flow Identifier (e.g. 5QI=1 for conversational voice, 5QI=9 for best-effort)
- PDU Session: data connectivity unit per UE per slice
- NWDAF: Network Data Analytics Function — produces predictions and analytics

## Decision rules
1. Always record the intent in the database before acting (record_intent tool).
2. Call the cn_nssmf_* and ran_nssmf_* tools in parallel when both domains
   are affected.
3. If resources are insufficient, apply graceful degradation:
   notify the operator, log the SLA violation, redistribute if policy allows.
4. Windows are handled by the background scheduler, not by you. Record
   window_start/window_end (ISO-8601 with UTC offset, resolved against the
   current date/time given with the intent) via record_intent. If
   record_intent answers status "scheduled", the window opens later: do NOT
   call cn_nssmf_*/ran_nssmf_* and do NOT call update_intent_status — the
   scheduler applies it at window_start and reverts it at window_end; just
   tell the operator when it will take effect. You never trigger reverts.
5. Call record_intent exactly once per operator request — never re-record the
   same request under a new intent_id.
6. For an intent you applied now, before your final response you MUST call
   update_intent_status exactly once, reflecting the true final status
   (applied / degraded / failed) — never rely on your text response alone.
7. A message starting with "## Event" is a report from a domain agent about
   an intent that already exists, not a new operator intent: do NOT call
   record_intent; act on the intent_id it names, as it instructs.
"""

app = Flask(__name__)


def _infer_status_for_intent(trace: list[dict], intent_id: int) -> str:
    """Fallback status when the model never called update_intent_status for
    this intent_id, from the domain agents' STRUCTURED status on their apply
    calls: 'failed' if an apply call errored or reported failed (or none was
    made), 'degraded' if any reported degraded, else 'applied'."""
    applies = [
        t for t in trace
        if t["tool"].startswith(("cn_nssmf_apply", "ran_nssmf_apply"))
        and t["input"].get("intent_id") == intent_id
    ]
    if not applies:
        return "failed"
    statuses = [t["result"].get("status") for t in applies]
    if any("error" in t["result"] for t in applies) or "failed" in statuses:
        return "failed"
    return "degraded" if "degraded" in statuses else "applied"



def run(intent_text: str) -> dict:
    """Drive the ReAct loop for one natural-language intent.

    Returns {"outcome": str, "trace": list[dict], "intent_ids": list[int]}.
    The trace and intent_ids are only consumed by the benchmark runner
    (agents/benchmark/run_benchmark.py, Section 5.4 metrics) — a normal
    operator call only cares about "outcome" — but /intent returns the whole
    dict rather than just the text because there is no other way to tell
    which DB rows (intents/policies/ran_allocations) a given call produced,
    or which tools were actually invoked (tool-selection accuracy, guardrail
    rejection rate, redundant-call rate all need the trace)."""
    print(f"[orchestrator] intent received: {intent_text}")
    print(f"[orchestrator] model: {MODEL} @ {OLLAMA_URL}")

    if RAG_ENABLED:
        chunks = retrieve(intent_text, top_k=3)
        print(f"[orchestrator] RAG retrieved {len(chunks)} chunks: {[c['id'] for c in chunks]}")
        context_block = format_context(chunks)
    else:
        print("[orchestrator] RAG disabled (ablation) — no context retrieved")
        context_block = ""
    # current date/time/zone first: the model has no clock, and windows like
    # "from 18h to 22h" / "tonight" are meaningless without it
    parts = [timeutil.prompt_header()]
    if context_block:
        parts.append(context_block)
    parts.append(f"## Intenção do operador\n{intent_text}")
    user_content = "\n\n".join(parts)

    result = react_loop(SYSTEM_PROMPT, user_content, get_tool_schemas(), dispatch_tool, tag="orchestrator")
    trace = result["trace"]

    # A single request can leave behind more than one intent_id, the model
    # sometimes calls record_intent more than once for the same operator
    # request (a separate, known redundancy issue; the dedup in react.py only
    # catches EXACT repeat calls, not this kind of semantic duplication).
    # Check each intent_id independently rather than bailing out the moment
    # ANY update_intent_status call is seen anywhere in the trace, otherwise
    # one intent getting updated masks another one left stuck at 'received'.

    created_ids = {
        t["result"]["intent_id"] for t in trace
        if t["tool"] == "record_intent" and "intent_id" in t.get("result", {})
    }
    updated_ids = {
        t["input"]["intent_id"] for t in trace
        if t["tool"] == "update_intent_status" and "intent_id" in t.get("input", {})
    }
    for intent_id in created_ids - updated_ids:
        if intent_status(intent_id) == "scheduled":
            continue  # owned by the scheduler until window_start
        status = _infer_status_for_intent(trace, intent_id)
        print(f"[orchestrator] safety-net: update_intent_status not called by the model "
              f"for intent {intent_id} — setting it to '{status}' deterministically")
        dispatch_tool("update_intent_status", {"intent_id": intent_id, "status": status})

    return {"outcome": result["final"], "trace": trace, "intent_ids": sorted(created_ids)}


@app.post("/intent")
def intent_endpoint():
    body = request.get_json(force=True) or {}
    text = body.get("intent") or body.get("text")
    if not text:
        return jsonify({"error": "body must contain 'intent'"}), 400
    try:
        return jsonify(run(text))
    except Exception as exc:  # surface any failure to the caller
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Use Case 1: events reported by the domain agents (cn-nssmf/monitor.py)
# ---------------------------------------------------------------------------

# how much above the predicted demand the new guarantee is sized
UC1_HEADROOM = float(os.getenv("UC1_HEADROOM", "0.10"))

EVENT_TEMPLATE = """## Event from CN-NSSMF — Use Case 1, predicted resource exhaustion
This is not an operator intent: do NOT call record_intent.
Intent {intent_id} (SST={sst}) currently guarantees {guaranteed_mbps} Mbps. The NWDAF
predicts {predicted_thp_mbps} Mbps of demand on this slice within {horizon_seconds} s
(predicted load {predicted_load}).
Scale the slice up for intent {intent_id}: call cn_nssmf_apply_qos AND
ran_nssmf_apply_resources with intent_id={intent_id}, sst={sst},
target_thp_mbps={new_target}. If the RAN can only meet part of it (degraded), keep the
best-effort allocation — the operator is notified automatically. Then call
update_intent_status for intent {intent_id} with the resulting status."""


def _update_event(event_id, outcome: str, extra: dict) -> None:
    if event_id is None:
        return
    conn = get_db_conn()
    with conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE events SET handled_at = NOW(), outcome = %s, "
            "payload = COALESCE(payload, '{}'::jsonb) || %s::jsonb WHERE id = %s",
            (outcome, json.dumps(extra), event_id),
        )


def _notify_operator(ev: dict, new_target: float, trace: list[dict]) -> None:
    """Graceful degradation, UC1: the expansion could only partly be met.
    'Notify the operator' = an sla_violation_predicted event (queryable) plus a
    WARNING line; redistribution across slices is not implemented."""
    ran = [t["result"] for t in trace if t["tool"] == "ran_nssmf_apply_resources"]
    payload = {"requested_mbps": new_target, "predicted_thp_mbps": ev.get("predicted_thp_mbps"),
               "ran_result": (ran[-1].get("result") if ran else None), "from_event": ev.get("event_id")}
    conn = get_db_conn()
    with conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO events (source, type, intent_id, sst, payload) "
            "VALUES ('orchestrator', 'sla_violation_predicted', %s, %s, %s)",
            (ev["intent_id"], ev.get("sst"), json.dumps(payload)),
        )
    print(f"[orchestrator] WARNING operator: intent {ev['intent_id']} (SST={ev.get('sst')}) can only be "
          f"partly scaled to {new_target} Mbps — predicted SLA violation recorded")


def handle_event(ev: dict) -> dict:
    """Let the LLM decide on a UC1 event, then settle the outcome from the
    domains' structured statuses (not from the model's prose)."""
    new_target = float(math.ceil(float(ev["predicted_thp_mbps"]) * (1 + UC1_HEADROOM)))
    print(f"[orchestrator] event {ev.get('event_id')}: {ev['type']} intent={ev['intent_id']} "
          f"-> scaling target to {new_target} Mbps")
    message = timeutil.prompt_header() + "\n\n" + EVENT_TEMPLATE.format(**{
        "predicted_load": None, "horizon_seconds": 60, **ev, "new_target": new_target})
    result = react_loop(SYSTEM_PROMPT, message, get_tool_schemas(), dispatch_tool, tag="orchestrator-event")
    trace = result["trace"]

    intent_id = ev["intent_id"]
    acted = any(t["tool"].startswith(("cn_nssmf_apply", "ran_nssmf_apply"))
                and t["input"].get("intent_id") == intent_id for t in trace)
    if not acted:
        outcome = "no_action"   # the intent keeps whatever was in force
    else:
        outcome = _infer_status_for_intent(trace, intent_id)
        dispatch_tool("update_intent_status", {"intent_id": intent_id, "status": outcome})
        if outcome == "degraded":
            _notify_operator(ev, new_target, trace)
    _update_event(ev.get("event_id"), outcome, {"new_target_mbps": new_target, "final": result["final"]})
    return {"outcome": outcome, "new_target_mbps": new_target, "trace": trace, "final": result["final"]}


@app.post("/event")
def event_endpoint():
    ev = request.get_json(force=True) or {}
    if ev.get("type") != "predicted_exhaustion":
        return jsonify({"error": f"unsupported event type: {ev.get('type')!r}"}), 400
    try:
        ev["intent_id"] = int(ev["intent_id"])
        float(ev["predicted_thp_mbps"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "event needs intent_id and predicted_thp_mbps"}), 400
    # answer the monitor right away; the ReAct run takes tens of seconds
    threading.Thread(target=handle_event, args=(ev,), name="uc1-event", daemon=True).start()
    return jsonify({"accepted": True, "event_id": ev.get("event_id")}), 202


@app.get("/health")
def health():
    return jsonify({"status": "ok", "agent": "orchestrator"})


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # one-shot CLI mode: python main.py "<intent>"
        print("\n--- OUTCOME ---")
        print(run(" ".join(sys.argv[1:]))["outcome"])
    else:
        # service mode
        scheduler.start()
        sla_monitor.start()
        print(f"[orchestrator] listening on :{PORT}  model={MODEL} @ {OLLAMA_URL}")
        app.run(host="0.0.0.0", port=PORT)
