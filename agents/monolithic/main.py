"""
Monolithic baseline for the benchmark (answers "why multi-agent?").

ONE ReAct agent with every tool the three MINAS agents have, in one loop:
the orchestrator's local tools (record_intent, update_intent_status,
get_sla_status), the CN-NSSMF's tools (query_nwdaf, get_core_kpis,
configure_qos, record_policy, revert_qos) and the RAN-NSSMF's tools
(get_ran_kpis, get_slice_load, estimate_capacity, allocate_prb, revert_prb).

Everything else is held equal to the multi-agent system, by reusing its code
rather than copying it: same model/inference settings (react.py), same RAG
(rag/retriever.py), same guardrails (each domain's dispatch_tool runs them),
same tool implementations and database, same date/time header, and the same
deterministic rules that do not depend on the agent structure:
  - an apply (configure_qos / allocate_prb) for a 'scheduled' intent is
    refused in code, like the orchestrator's dispatcher does;
  - a safety net sets the final status from the structured results of the
    apply calls when the model did not call update_intent_status.
What differs is only the structure: no decomposition into directives, no MCP
hop, no per-directive tool scoping — the model sees all 13 tools at once.

POST /intent {"intent": "..."} -> {"outcome", "trace", "intent_ids"}, the same
contract as the orchestrator, so run_benchmark.run_one() can drive it.

Run inside the agent image with agents/ mounted at /app/agents, e.g.:
  docker run -d --name monolithic --network tcc_minas_minas-net -p 8010:8010 \
    -v <agents>:/app/agents -w /app/agents/monolithic \
    -e POSTGRES_HOST=10.11.0.40 ... minas-agent:local python main.py
"""

from __future__ import annotations

import importlib.util
import os
import sys

AGENTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, AGENTS)
sys.path.insert(0, os.path.join(AGENTS, "cn-nssmf"))   # cn tools import nwdaf_client

from flask import Flask, jsonify, request  # noqa: E402

import timeutil  # noqa: E402
from rag.retriever import format_context, retrieve  # noqa: E402
from react import MODEL, OLLAMA_URL, react_loop  # noqa: E402


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


orch = _load("orch_tools", os.path.join(AGENTS, "orchestrator", "tools.py"))
cn = _load("cn_tools", os.path.join(AGENTS, "cn-nssmf", "tools.py"))
ran = _load("ran_tools", os.path.join(AGENTS, "ran-nssmf", "tools.py"))

PORT = int(os.getenv("MONOLITHIC_PORT", "8010"))
RAG_ENABLED = os.getenv("RAG_ENABLED", "true").strip().lower() not in ("false", "0", "no")

ORCH_TOOLS = {"record_intent", "update_intent_status", "get_sla_status"}
CN_TOOLS = {"query_nwdaf", "get_core_kpis", "configure_qos", "record_policy", "revert_qos"}
RAN_TOOLS = {"get_ran_kpis", "get_slice_load", "estimate_capacity", "allocate_prb", "revert_prb"}
APPLY_TOOLS = {"configure_qos", "allocate_prb"}

TOOL_SCHEMAS = (
    [s for s in orch.LOCAL_TOOL_SCHEMAS if s["function"]["name"] in ORCH_TOOLS]
    + [s for s in cn.TOOL_SCHEMAS if s["function"]["name"] in CN_TOOLS]
    + [s for s in ran.TOOL_SCHEMAS if s["function"]["name"] in RAN_TOOLS]
)

SCHEDULED_NOTE = ("Scheduled: do NOT call configure_qos/allocate_prb for this intent now and do "
                  "not call update_intent_status — the scheduler applies it at window_start and "
                  "reverts it at window_end. Tell the operator it is scheduled and stop.")

# The orchestrator's prompt with the domain agents' rules folded in: same
# content, one agent.
SYSTEM_PROMPT = """You are MINAS, a single agent for autonomous 5G network slice management.
You handle the whole intent yourself: interpretation, the 5G Core and the RAN.

## Role
- Receive operator intents expressed in natural language.
- Interpret the intent and extract the target slice (SST), QoS objective,
  and optional enforcement window (window_start / window_end).
- Configure both domains yourself with your tools:
    * 5G Core (PCF/SMF QoS per slice): query_nwdaf, get_core_kpis,
      configure_qos, record_policy
    * RAN (PRB share per slice at the gNB): get_ran_kpis, get_slice_load,
      estimate_capacity, allocate_prb

## Slices in operation
- SST=1 (eMBB)  — streaming / scheduled QoS (Use Case 2)
- SST=2 (URLLC) — mission-critical / predictive resource adjustment (Use Case 1)

## 3GPP reference vocabulary (TS 28.312 / TS 28.552 / TS 23.501)
- S-NSSAI {sst, sd}; GBR / MBR; 5QI (e.g. 5QI=1 voice, 5QI=9 best-effort)
- PDU Session; PRB (Physical Resource Block); NWDAF analytics (SLICE_LOAD_LEVEL)

## Decision rules
1. Always record the intent in the database before acting (record_intent tool).
2. When both domains are affected, configure both: the Core with configure_qos
   (for SST=2 call query_nwdaf first) and the RAN with estimate_capacity then
   allocate_prb.
3. Core: convert a throughput target in Mbps into GBR; set MBR = GBR unless told
   otherwise; use the default 5QI for the SST when none is given. configure_qos
   records the policy itself; call record_policy only to correct it.
4. RAN: before allocate_prb, always call estimate_capacity. If it is not
   feasible, still apply the best-effort allocation; the intent is then
   "degraded" (state the shortfall in Mbps).
5. Windows are handled by the background scheduler, not by you. Record
   window_start/window_end (ISO-8601 with UTC offset, resolved against the
   current date/time given with the intent) via record_intent. If
   record_intent answers status "scheduled", the window opens later: do NOT
   call configure_qos/allocate_prb and do NOT call update_intent_status — the
   scheduler applies it at window_start and reverts it at window_end; just
   tell the operator when it will take effect. You never trigger reverts.
6. Call record_intent exactly once per operator request — never re-record the
   same request under a new intent_id.
7. For an intent you applied now, before your final response you MUST call
   update_intent_status exactly once, reflecting the true final status
   (applied / degraded / failed) — never rely on your text response alone.
"""


def dispatch(name: str, params: dict) -> dict:
    if name in APPLY_TOOLS and orch.intent_status(params.get("intent_id")) == "scheduled":
        return {"status": "scheduled", "blocked": True, "intent_id": params.get("intent_id"),
                "note": SCHEDULED_NOTE}
    if name in ORCH_TOOLS:
        out = orch.dispatch_tool(name, params)
        if name == "record_intent" and isinstance(out, dict) and out.get("status") == "scheduled":
            out = {**out, "note": SCHEDULED_NOTE}
        return out
    if name in CN_TOOLS:
        return cn.dispatch_tool(name, params)
    if name in RAN_TOOLS:
        return ran.dispatch_tool(name, params)
    return {"error": f"unknown tool: {name}"}


def _infer_status(trace: list[dict], intent_id: int) -> str:
    """Same rule as the orchestrator's safety net (_infer_status_for_intent),
    applied to the direct apply calls instead of the MCP directives."""
    # like the domains' _directive_status, each domain's LAST apply call counts
    last: dict[str, dict] = {}
    for t in trace:
        if t["tool"] in APPLY_TOOLS and t["input"].get("intent_id") == intent_id:
            last[t["tool"]] = t["result"]
    if not last:
        return "failed"
    statuses = [r.get("status") for r in last.values()]
    if any("error" in r for r in last.values()) or "failed" in statuses:
        return "failed"
    return "degraded" if "degraded" in statuses else "applied"


def run(intent_text: str) -> dict:
    if RAG_ENABLED:
        context_block = format_context(retrieve(intent_text, top_k=3))
    else:
        context_block = ""
    parts = [timeutil.prompt_header()]
    if context_block:
        parts.append(context_block)
    parts.append(f"## Intenção do operador\n{intent_text}")

    result = react_loop(SYSTEM_PROMPT, "\n\n".join(parts), TOOL_SCHEMAS, dispatch, tag="monolithic")
    trace = result["trace"]
    created = {t["result"]["intent_id"] for t in trace
               if t["tool"] == "record_intent" and "intent_id" in t.get("result", {})}
    updated = {t["input"]["intent_id"] for t in trace
               if t["tool"] == "update_intent_status" and "intent_id" in t.get("input", {})}
    for iid in created - updated:
        if orch.intent_status(iid) == "scheduled":
            continue
        status = _infer_status(trace, iid)
        print(f"[monolithic] safety-net: intent {iid} -> {status}")
        orch.dispatch_tool("update_intent_status", {"intent_id": iid, "status": status})
    return {"outcome": result["final"], "trace": trace, "intent_ids": sorted(created)}


app = Flask(__name__)


@app.post("/intent")
def intent_endpoint():
    body = request.get_json(force=True) or {}
    text = body.get("intent") or body.get("text")
    if not text:
        return jsonify({"error": "body must contain 'intent'"}), 400
    try:
        return jsonify(run(text))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@app.get("/health")
def health():
    return jsonify({"status": "ok", "agent": "monolithic", "tools": len(TOOL_SCHEMAS)})


if __name__ == "__main__":
    print(f"[monolithic] listening on :{PORT}  model={MODEL} @ {OLLAMA_URL}  tools={len(TOOL_SCHEMAS)}")
    app.run(host="0.0.0.0", port=PORT, threaded=False)
