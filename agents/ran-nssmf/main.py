"""
MINAS RAN-NSSMF Agent — Radio Access Network Slice Subnet Management Function

Receives operational directives from the Orchestrator over HTTP, reasons about
them with an LLM (ReAct, no fine-tuning) and adjusts radio resources per slice:
PRB allocation at the gNB, RAN telemetry and per-slice load.

Paradigm  : ReAct (Reasoning and Acting) — no fine-tuning
Backend   : Ollama (local inference server) — see agents/react.py
Transport : HTTP POST /directive  (called by the orchestrator's invoke_ran_nssmf tool)
"""

import json
import os
import sys

# make agents/ importable (shared db.py + react.py) whether run via Docker or `cd agents/ran-nssmf`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request

from react import MODEL, OLLAMA_URL, react_loop
from tools import TOOL_SCHEMAS, dispatch_tool

PORT = int(os.getenv("RAN_NSSMF_PORT", "8002"))

SYSTEM_PROMPT = """You are the MINAS RAN-NSSMF, the domain agent responsible for
Radio Access Network slice resources inside a Multi-Agent System for autonomous
slice management.

## Role
- Receive a single operational directive from the Orchestrator. You never talk to
  the operator directly, and never to the CN-NSSMF — all coordination is mediated
  by the Orchestrator.
- Translate the directive into radio resource parameters at the gNB: the PRB /
  resource share allocated to each slice (S-NSSAI).
- Read RAN telemetry (RSRP, SINR, MCS, PRB usage, throughput) and the per-slice
  load index.
- Report the resulting SLA compliance state back to the Orchestrator as your
  final answer. If the radio budget cannot meet the target, say so explicitly —
  the Orchestrator, not you, decides on graceful degradation.

## Slices in operation
- SST=1 (eMBB)  — streaming / scheduled QoS (Use Case 2)
- SST=2 (URLLC) — mission-critical / predictive resource adjustment (Use Case 1)

## Vocabulary
- S-NSSAI {sst, sd}; PRB (Physical Resource Block); MCS; RSRP / SINR
- gNB, DU/CU; RRM (Radio Resource Management)
- Radio KPIs per 3GPP TS 28.552

## Directive actions you may receive
- apply_resources  — size and apply a PRB allocation for a slice: call
                     estimate_capacity, then allocate_prb
- revert_resources — restore the previous allocation for the intent (revert_prb)
- check_sla        — read get_ran_kpis / get_slice_load and judge SLA compliance

## Decision rules
1. Before allocate_prb, always call estimate_capacity. If it is not feasible,
   still apply the best-effort allocation and end with status "degraded",
   stating the shortfall in Mbps.
2. Finish with a concise SLA status line: applied / degraded / failed / reverted,
   plus the key numbers (PRBs, supported throughput, shortfall).
"""

app = Flask(__name__)


def handle_directive(directive: dict) -> dict:
    """Run the ReAct loop for one orchestrator directive; return a JSON-able dict."""
    print(f"[ran-nssmf] directive received: {json.dumps(directive)}")
    user_msg = (
        "Directive from the Orchestrator:\n"
        f"{json.dumps(directive, indent=2)}\n\n"
        "Carry it out and report the SLA status."
    )
    out = react_loop(SYSTEM_PROMPT, user_msg, TOOL_SCHEMAS, dispatch_tool, tag="ran-nssmf")
    return {
        "agent": "ran-nssmf",
        "intent_id": directive.get("intent_id"),
        "action": directive.get("action"),
        "result": out["final"],
        "trace": out["trace"],
    }


@app.post("/directive")
def directive_endpoint():
    directive = request.get_json(force=True) or {}
    try:
        return jsonify(handle_directive(directive))
    except Exception as exc:  # surface any failure to the orchestrator
        return jsonify({"agent": "ran-nssmf", "error": str(exc)}), 500


@app.get("/health")
def health():
    return jsonify({"status": "ok", "agent": "ran-nssmf"})


if __name__ == "__main__":
    print(f"[ran-nssmf] listening on :{PORT}  model={MODEL} @ {OLLAMA_URL}")
    app.run(host="0.0.0.0", port=PORT)
