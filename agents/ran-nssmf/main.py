"""
MINAS RAN-NSSMF Agent — Radio Access Network Slice Subnet Management Function

Exposed to the Orchestrator as an MCP server (Model Context Protocol,
streamable-HTTP transport). Each MCP tool corresponds to one directive
action; when invoked it runs the agent's own ReAct loop (no fine-tuning,
local LLM via Ollama) and adjusts radio resources per slice: PRB
allocation at the gNB, RAN telemetry and per-slice load.

Coordination is mediated by the Orchestrator over MCP — this agent never
talks to the operator or to the CN-NSSMF directly.

Paradigm  : ReAct (Reasoning and Acting) — no fine-tuning
Backend   : Ollama (local inference server) — see agents/react.py
Transport : MCP over streamable HTTP  (server endpoint  :8002/mcp)
"""

import json
import os
import sys

# make agents/ importable (shared db.py + react.py) whether run via Docker or `cd agents/ran-nssmf`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP

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

mcp = FastMCP("ran-nssmf", host="0.0.0.0", port=PORT)


def _run_directive(action: str, intent_id: int, sst: int,
                   target_thp_mbps: float | None = None) -> dict:
    """Build a directive dict and drive the ReAct loop for it — the same
    body the old `POST /directive` handler had, now reached over MCP."""
    directive: dict = {"intent_id": intent_id, "action": action, "sst": sst}
    if target_thp_mbps is not None:
        directive["target_thp_mbps"] = target_thp_mbps

    print(f"[ran-nssmf] directive received (mcp): {json.dumps(directive)}")
    user_msg = (
        "Directive from the Orchestrator:\n"
        f"{json.dumps(directive, indent=2)}\n\n"
        "Carry it out and report the SLA status."
    )
    out = react_loop(SYSTEM_PROMPT, user_msg, TOOL_SCHEMAS, dispatch_tool, tag="ran-nssmf")
    return {
        "agent": "ran-nssmf",
        "intent_id": intent_id,
        "action": action,
        "result": out["final"],
        "trace": out["trace"],
    }


@mcp.tool()
def apply_resources(intent_id: int, sst: int, target_thp_mbps: float | None = None) -> dict:
    """Size and apply a PRB allocation for a slice at the gNB. The agent
    calls estimate_capacity first; if the radio budget cannot cover the
    target it still applies best-effort and reports status 'degraded'."""
    return _run_directive("apply_resources", intent_id, sst, target_thp_mbps)


@mcp.tool()
def revert_resources(intent_id: int, sst: int) -> dict:
    """Restore the PRB allocation that was in place before this intent."""
    return _run_directive("revert_resources", intent_id, sst)


@mcp.tool()
def check_sla(intent_id: int, sst: int) -> dict:
    """Read RAN KPIs and the per-slice load index and judge SLA compliance."""
    return _run_directive("check_sla", intent_id, sst)


def _register_health() -> None:
    """Best-effort GET /health — skipped on mcp versions without custom_route."""
    try:
        from starlette.responses import JSONResponse

        @mcp.custom_route("/health", methods=["GET"])
        async def _health(_req):  # noqa: ANN001
            return JSONResponse({"status": "ok", "agent": "ran-nssmf", "transport": "mcp"})
    except Exception as exc:  # noqa: BLE001
        print(f"[ran-nssmf] /health route unavailable: {exc}")


_register_health()


if __name__ == "__main__":
    print(f"[ran-nssmf] MCP server on :{PORT}/mcp   model={MODEL} @ {OLLAMA_URL}")
    mcp.run(transport="streamable-http")
