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
from react import MODEL, OLLAMA_URL, react_loop
from tools import dispatch_tool, get_tool_schemas

PORT = int(os.getenv("ORCHESTRATOR_PORT", "8000"))

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
5. Report final outcome (applied / degraded / failed / reverted) back to the operator.
"""

app = Flask(__name__)


def run(intent_text: str) -> str:
    """Drive the ReAct loop for one natural-language intent; return the outcome text."""
    print(f"[orchestrator] intent received: {intent_text}")
    print(f"[orchestrator] model: {MODEL} @ {OLLAMA_URL}")
    return react_loop(SYSTEM_PROMPT, intent_text, get_tool_schemas(), dispatch_tool, tag="orchestrator")["final"]


@app.post("/intent")
def intent_endpoint():
    body = request.get_json(force=True) or {}
    text = body.get("intent") or body.get("text")
    if not text:
        return jsonify({"error": "body must contain 'intent'"}), 400
    try:
        return jsonify({"outcome": run(text)})
    except Exception as exc:  # surface any failure to the caller
        return jsonify({"error": str(exc)}), 500


@app.get("/health")
def health():
    return jsonify({"status": "ok", "agent": "orchestrator"})


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # one-shot CLI mode: python main.py "<intent>"
        print("\n--- OUTCOME ---")
        print(run(" ".join(sys.argv[1:])))
    else:
        # service mode
        scheduler.start()
        print(f"[orchestrator] listening on :{PORT}  model={MODEL} @ {OLLAMA_URL}")
        app.run(host="0.0.0.0", port=PORT)
