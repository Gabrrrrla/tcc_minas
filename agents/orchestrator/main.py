"""
MINAS Orchestrator Agent
Receives operator intents in natural language, decomposes them into
directives, and coordinates CN-NSSMF and RAN-NSSMF via tool-calling.

Paradigm : ReAct (Reasoning and Acting) — no fine-tuning
Domain   : injected via system prompt + tool descriptions
Model    : configurable via MINAS_MODEL env var (default: llama3.1:70b)
Backend  : Ollama (local inference server, no external API dependency)
"""

import json
import os
import sys

import requests
from dotenv import load_dotenv

from tools import TOOL_SCHEMAS, dispatch_tool

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL      = os.getenv("MINAS_MODEL", "llama3.1:70b")

SYSTEM_PROMPT = """You are the MINAS Orchestrator, the single coordination point of a
Multi-Agent System for autonomous 5G network slice management.

## Role
- Receive operator intents expressed in natural language.
- Interpret the intent and extract the target slice (SST), QoS objective,
  and optional enforcement window (window_start / window_end).
- Decompose the intent into directives and invoke the domain agents:
    * CN-NSSMF  — manages 5G Core functions (AMF, SMF, PCF, UPF, NWDAF)
    * RAN-NSSMF — manages Radio Access Network resources (srsRAN gNB)
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
2. Invoke CN-NSSMF and RAN-NSSMF in parallel when both domains are affected.
3. If resources are insufficient, apply graceful degradation:
   notify the operator, log the SLA violation, redistribute if policy allows.
4. When a time window expires, send revert directives to both domain agents.
5. Report final outcome (applied / degraded / failed / reverted) back to the operator.
"""


def _call_ollama(messages: list[dict]) -> dict:
    resp = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model":   MODEL,
            "messages": messages,
            "tools":    TOOL_SCHEMAS,
            "stream":   False,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def run(intent_text: str) -> str:
    """
    Main ReAct loop: receives a natural-language intent and drives the
    orchestrator until the intent is fully resolved or fails.
    Returns a human-readable summary of the outcome.
    """
    print(f"[orchestrator] intent received: {intent_text}")
    print(f"[orchestrator] model: {MODEL} @ {OLLAMA_URL}")

    messages: list[dict] = [
        {"role": "system",  "content": SYSTEM_PROMPT},
        {"role": "user",    "content": intent_text},
    ]

    while True:
        response    = _call_ollama(messages)
        assistant   = response["message"]
        tool_calls  = assistant.get("tool_calls") or []

        # Append assistant turn (store only role + content for history)
        messages.append({
            "role":    "assistant",
            "content": assistant.get("content") or "",
        })

        # No tool calls → final answer
        if not tool_calls:
            final_text = assistant.get("content", "")
            print(f"[orchestrator] done: {final_text}")
            return final_text

        # Process tool calls and collect results
        tool_results = []
        for call in tool_calls:
            func       = call["function"]
            tool_name  = func["name"]
            tool_input = json.loads(func["arguments"]) if isinstance(func["arguments"], str) else func["arguments"]

            print(f"[orchestrator] tool_use  → {tool_name}({json.dumps(tool_input)})")
            result = dispatch_tool(tool_name, tool_input)
            print(f"[orchestrator] tool_result ← {json.dumps(result)}")

            tool_results.append(json.dumps(result))

        # Feed all results back as a single user turn
        messages.append({
            "role":    "tool",
            "content": "\n".join(tool_results),
        })


if __name__ == "__main__":
    intent = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else (
        "Aumentar a taxa de dados garantida para a fatia de streaming "
        "de 10 Mbps para 20 Mbps entre 18h e 22h."
    )
    outcome = run(intent)
    print("\n--- OUTCOME ---")
    print(outcome)
