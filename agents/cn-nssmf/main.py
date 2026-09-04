"""
MINAS CN-NSSMF Agent — Core Network Slice Subnet Management Function

Receives operational directives from the Orchestrator over HTTP, reasons about
them with an LLM (ReAct, no fine-tuning) and enforces them on the 5G Core:
QoS reconfiguration at PCF/SMF and predictive analytics from the NWDAF.

Paradigm  : ReAct (Reasoning and Acting) — no fine-tuning
Backend   : Ollama (local inference server) — see agents/react.py
Transport : HTTP POST /directive  (called by the orchestrator's invoke_cn_nssmf tool)
"""

import json
import os
import sys

# make agents/ importable (shared db.py + react.py) whether run via Docker or `cd agents/cn-nssmf`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request

from react import MODEL, OLLAMA_URL, react_loop
from tools import TOOL_SCHEMAS, dispatch_tool

PORT = int(os.getenv("CN_NSSMF_PORT", "8001"))

SYSTEM_PROMPT = """You are the MINAS CN-NSSMF, the domain agent responsible for
5G Core Network slice resources inside a Multi-Agent System for autonomous slice
management.

## Role
- Receive a single operational directive from the Orchestrator. You never talk to
  the operator directly, and never to the RAN-NSSMF — all coordination is mediated
  by the Orchestrator.
- Reason about the directive and enforce it on the 5G Core control plane:
    * PCF — policy rules (PCC rule, 5QI, ARP) per S-NSSAI
    * SMF — session parameters (Session-AMBR, QoS Flow GBR/MBR) per DNN / slice
- Obtain telemetry and predictions from the NWDAF via its normative interfaces
  (Nnwdaf_AnalyticsInfo, Nnwdaf_EventsSubscription).
- Report the resulting SLA compliance state back to the Orchestrator as your
  final answer.

## Slices in operation
- SST=1 (eMBB)  — streaming / scheduled QoS (Use Case 2)
- SST=2 (URLLC) — mission-critical / predictive resource adjustment (Use Case 1)

## 3GPP reference vocabulary
- S-NSSAI {sst, sd}; DNN; 5QI; GBR / MBR; Session-AMBR; Slice-AMBR
- PCC rule (TS 23.503); QoS Flow / QFI (TS 23.501)
- NWDAF analytics IDs (TS 23.288 §6): SLICE_LOAD_LEVEL, NF_LOAD,
  USER_DATA_CONGESTION, ABNORMAL_BEHAVIOUR
- Intent-driven management services (TS 28.312)

## Directive actions you may receive
- apply_qos    — reconfigure QoS for a slice; in a predictive scenario (SST=2)
                 call query_nwdaf first, then configure_qos, then record_policy
- revert_qos   — restore the previous configuration for the intent
- query_nwdaf  — return an analytics / prediction report only
- check_sla    — read core KPIs for the slice and judge SLA compliance

## Decision rules
1. Convert a throughput target in Mbps into GBR (guaranteed); set MBR = GBR unless
   told otherwise. Use the default 5QI for the SST when none is given.
2. After a successful configure_qos, always call record_policy.
3. If the Core cannot satisfy the target, do not fail silently: end with status
   "degraded" and state the shortfall so the Orchestrator can decide.
4. Finish with a concise SLA status line: applied / degraded / failed / reverted,
   plus the key numbers.
"""

app = Flask(__name__)


def handle_directive(directive: dict) -> dict:
    """Run the ReAct loop for one orchestrator directive; return a JSON-able dict."""
    print(f"[cn-nssmf] directive received: {json.dumps(directive)}")
    user_msg = (
        "Directive from the Orchestrator:\n"
        f"{json.dumps(directive, indent=2)}\n\n"
        "Carry it out and report the SLA status."
    )
    out = react_loop(SYSTEM_PROMPT, user_msg, TOOL_SCHEMAS, dispatch_tool, tag="cn-nssmf")
    return {
        "agent": "cn-nssmf",
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
        return jsonify({"agent": "cn-nssmf", "error": str(exc)}), 500


@app.get("/health")
def health():
    return jsonify({"status": "ok", "agent": "cn-nssmf"})


if __name__ == "__main__":
    print(f"[cn-nssmf] listening on :{PORT}  model={MODEL} @ {OLLAMA_URL}")
    app.run(host="0.0.0.0", port=PORT)
