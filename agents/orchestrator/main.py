"""
MINAS Orchestrator Agent
Receives operator intents in natural language, decomposes them into
directives, and coordinates CN-NSSMF and RAN-NSSMF via tool-calling.

Paradigm  : ReAct (Reasoning and Acting) — no fine-tuning
Domain    : injected via system prompt + tool descriptions
Backend   : Ollama (local inference server) — see agents/react.py
Transport : HTTP POST /intent  (operator / tests)   +   CLI  (python main.py "<intent>")
"""

import os
import sys

# make agents/ importable (shared db.py + react.py) whether run via Docker or `cd agents/orchestrator`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request

import scheduler
from rag.retriever import format_context, retrieve
from react import MODEL, OLLAMA_URL, react_loop
from tools import dispatch_tool, get_tool_schemas

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
4. Windowed intents (window_end) revert automatically once the window closes —
   handled by the background scheduler, not by you. You never need to schedule
   or trigger a revert yourself; just record window_end via record_intent.
5. Call record_intent exactly once per operator request — never re-record the
   same request under a new intent_id.
6. Before your final response, you MUST call update_intent_status exactly
   once, reflecting the true final status (applied / degraded / failed) —
   never rely on your text response alone to report the outcome.
"""

app = Flask(__name__)


def _infer_status_for_intent(trace: list[dict], intent_id: int) -> str:
    """Heuristic fallback status when the model never called
    update_intent_status for this intent_id: 'applied' if every
    cn_nssmf_*/ran_nssmf_* call addressed to this intent_id came back without
    an "error" key, 'failed' otherwise. Deliberately simple — it only has to
    get the intent out of 'received'/'negotiating' so the scheduler can see
    it; it doesn't distinguish 'applied' from 'degraded' (that needs
    structured status from the domain agents, which they don't return today
    — see HANDOVER-2026-09-12.md)."""
    remote_calls = [
        t for t in trace
        if t["tool"].startswith(("cn_nssmf_", "ran_nssmf_")) and t["input"].get("intent_id") == intent_id
    ]
    if not remote_calls:
        return "failed"
    return "failed" if any("error" in t["result"] for t in remote_calls) else "applied"



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
    user_content = f"{context_block}\n\n## Intenção do operador\n{intent_text}" if context_block else intent_text

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
        print(f"[orchestrator] listening on :{PORT}  model={MODEL} @ {OLLAMA_URL}")
        app.run(host="0.0.0.0", port=PORT)
