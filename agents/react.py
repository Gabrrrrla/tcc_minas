"""
Shared Ollama client and ReAct loop for MINAS LLM agents.

call_ollama — one /api/chat round against the local Ollama server.
react_loop  — reason/act cycle: call the model, run any tool calls it emits,
              feed the results back, repeat until the model answers without a
              tool call (or REACT_MAX_STEPS is hit).

Environment: OLLAMA_URL (default http://localhost:11434),
             MINAS_MODEL (default qwen2.5:7b — see env.example for why this
             is the default instead of the telecom-tuned OTel-LLM-E4B-IT),
             REACT_MAX_STEPS (default 16).
"""

from __future__ import annotations

import json
import os
from typing import Callable

import requests
from dotenv import load_dotenv

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL      = os.getenv("MINAS_MODEL", "qwen2.5:7b")
MAX_STEPS  = int(os.getenv("REACT_MAX_STEPS", "16"))


def call_ollama(messages: list[dict], tools: list[dict]) -> dict:
    resp = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": MODEL, "messages": messages, "tools": tools, "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def react_loop(
    system_prompt: str,
    user_content: str,
    tools: list[dict],
    dispatch: Callable[[str, dict], dict],
    tag: str = "agent",
) -> dict:
    """Run the ReAct cycle. Returns {"final": <text>, "trace": [<tool calls>]}."""
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_content},
    ]
    trace: list[dict] = []
    # Cache of (name, sorted-args) -> result already dispatched this loop, so
    # an exact repeat reuses the prior result instead of re-executing it (a
    # write tool like record_policy/allocate_prb would otherwise insert a new
    # duplicate row for identical arguments — seen live in the 12/09 smoke
    # test: 8 duplicate record_policy rows for one intent). Genuinely
    # different arguments still dispatch normally.
    seen: dict[str, dict] = {}

    for _ in range(MAX_STEPS):
        assistant  = call_ollama(messages, tools)["message"]
        tool_calls = assistant.get("tool_calls") or []
        # Must include tool_calls here, not just content: dropping it left the
        # model's own history showing an empty assistant turn immediately
        # followed by a tool result attached to no call, which loses the
        # model's own record of what it already invoked by the next turn —
        # a likely cause of the pattern (seen live 22/09/2026) where a model
        # calls one tool correctly, then narrates the rest as prose/JSON text
        # instead of continuing to call tools.
        assistant_message = {"role": "assistant", "content": assistant.get("content") or ""}
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        messages.append(assistant_message)

        # No tool calls → final answer
        if not tool_calls:
            final = assistant.get("content", "")
            print(f"[{tag}] done: {final}")
            return {"final": final, "trace": trace}

        # Run every tool call, collect results as one tool turn
        results = []
        for call in tool_calls:
            fn   = call["function"]
            name = fn["name"]
            args = json.loads(fn["arguments"]) if isinstance(fn["arguments"], str) else fn["arguments"]
            call_key = f"{name}|{json.dumps(args, sort_keys=True)}"

            if call_key in seen:
                result = seen[call_key]
                print(f"[{tag}] tool_use  → {name}({json.dumps(args)}) (repetida, cache)")
            else:
                print(f"[{tag}] tool_use  → {name}({json.dumps(args)})")
                result = dispatch(name, args)
                seen[call_key] = result
            print(f"[{tag}] tool_result ← {json.dumps(result)}")

            trace.append({"tool": name, "input": args, "result": result})
            results.append(json.dumps(result))

        messages.append({"role": "tool", "content": "\n".join(results)})

    return {"final": "step limit reached without a final answer", "trace": trace}
