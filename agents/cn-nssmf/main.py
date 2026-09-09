"""
MINAS CN-NSSMF Agent — Core Network Slice Subnet Management Function

Exposed to the Orchestrator as an MCP server (Model Context Protocol,
streamable-HTTP transport). Each MCP tool corresponds to one directive
action; when invoked it runs the agent's own ReAct loop (no fine-tuning,
local LLM via Ollama) and enforces the result on the 5G Core: QoS
reconfiguration at PCF/SMF and predictive analytics from the NWDAF.

Coordination is mediated by the Orchestrator over MCP — this agent never
talks to the operator or to the RAN-NSSMF directly.

Paradigm  : ReAct (Reasoning and Acting) — no fine-tuning
Backend   : Ollama (local inference server) — see agents/react.py
Transport : MCP over streamable HTTP  (server endpoint  :8001/mcp)
"""

import json
import os
import sys

# make agents/ importable (shared db.py + react.py) whether run via Docker or `cd agents/cn-nssmf`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP

from react import MODEL, OLLAMA_URL, react_loop
from tools import TOOL_SCHEMAS, dispatch_tool

PORT = int(os.getenv("CN_NSSMF_PORT", "8001"))

SYSTEM_PROMPT = """You are the MINAS CN-NSSMF, the domain agent responsible for
5G Core Network slice resources inside a Multi-Agent System for autonomous slice
management.

## Role
- Receive a single operational directive from the Orchestrator. You never talk to
  the operator directly, and never to the RAN-NSSMF, all coordination is mediated
  by the Orchestrator.
- Reason about the directive and enforce it on the 5G Core control plane:
    * PCF policy rules (PCC rule, 5QI, ARP) per S-NSSAI
    * SMF session parameters (Session-AMBR, QoS Flow GBR/MBR) per DNN / slice
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

mcp = FastMCP("cn-nssmf", host="0.0.0.0", port=PORT)


def _run_directive(action: str, intent_id: int, sst: int,
                   target_thp_mbps: float | None = None,
                   window_end: str | None = None) -> dict:
    """Build a directive dict and drive the ReAct loop for it — the same
    body the old `POST /directive` handler had, now reached over MCP."""
    directive: dict = {"intent_id": intent_id, "action": action, "sst": sst}
    if target_thp_mbps is not None:
        directive["target_thp_mbps"] = target_thp_mbps
    if window_end is not None:
        directive["window_end"] = window_end

    print(f"[cn-nssmf] directive received (mcp): {json.dumps(directive)}")
    user_msg = (
        "Directive from the Orchestrator:\n"
        f"{json.dumps(directive, indent=2)}\n\n"
        "Carry it out and report the SLA status."
    )
    out = react_loop(SYSTEM_PROMPT, user_msg, TOOL_SCHEMAS, dispatch_tool, tag="cn-nssmf")
    return {
        "agent": "cn-nssmf",
        "intent_id": intent_id,
        "action": action,
        "result": out["final"],
        "trace": out["trace"],
    }


@mcp.tool()
def apply_qos(intent_id: int, sst: int, target_thp_mbps: float | None = None,
              window_end: str | None = None) -> dict:
    """Reconfigure QoS for a slice on the 5G Core (PCF policy rule + SMF
    session parameters). In a predictive scenario (SST=2) the agent queries
    the NWDAF first, then configures QoS, then records the policy."""
    return _run_directive("apply_qos", intent_id, sst, target_thp_mbps, window_end)


@mcp.tool()
def revert_qos(intent_id: int, sst: int) -> dict:
    """Restore the QoS configuration that was in place before this intent's
    policy was applied. Called when a time window expires."""
    return _run_directive("revert_qos", intent_id, sst)


@mcp.tool()
def query_nwdaf(intent_id: int, sst: int) -> dict:
    """Return an NWDAF analytics / prediction report for the slice, without
    changing any configuration."""
    return _run_directive("query_nwdaf", intent_id, sst)


@mcp.tool()
def check_sla(intent_id: int, sst: int) -> dict:
    """Read core-network KPIs for the slice and judge SLA compliance."""
    return _run_directive("check_sla", intent_id, sst)


def _register_health() -> None:
    """Best-effort GET /health — skipped on mcp versions without custom_route."""
    try:
        from starlette.responses import JSONResponse

        @mcp.custom_route("/health", methods=["GET"])
        async def _health(_req):  # noqa: ANN001
            return JSONResponse({"status": "ok", "agent": "cn-nssmf", "transport": "mcp"})
    except Exception as exc:  # noqa: BLE001
        print(f"[cn-nssmf] /health route unavailable: {exc}")


_register_health()


if __name__ == "__main__":
    print(f"[cn-nssmf] MCP server on :{PORT}/mcp   model={MODEL} @ {OLLAMA_URL}")
    mcp.run(transport="streamable-http")
