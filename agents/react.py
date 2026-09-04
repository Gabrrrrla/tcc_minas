"""
Shared Ollama client and ReAct loop for MINAS LLM agents.

call_ollama — one /api/chat round against the local Ollama server.
react_loop  — reason/act cycle: call the model, run any tool calls it emits,
              feed the results back, repeat until the model answers without a
              tool call (or REACT_MAX_STEPS is hit).

Environment: OLLAMA_URL (default http://localhost:11434),
             MINAS_MODEL (default llama3.1:70b),
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
MODEL      = os.getenv("MINAS_MODEL", "llama3.1:70b")
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

    for _ in range(MAX_STEPS):
        assistant  = call_ollama(messages, tools)["message"]
        tool_calls = assistant.get("tool_calls") or []
        messages.append({"role": "assistant", "content": assistant.get("content") or ""})

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

            print(f"[{tag}] tool_use  → {name}({json.dumps(args)})")
            result = dispatch(name, args)
            print(f"[{tag}] tool_result ← {json.dumps(result)}")

            trace.append({"tool": name, "input": args, "result": result})
            results.append(json.dumps(result))

        messages.append({"role": "tool", "content": "\n".join(results)})

    return {"final": "step limit reached without a final answer", "trace": trace}
