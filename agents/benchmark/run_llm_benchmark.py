"""
agents/benchmark/run_llm_benchmark.py
--------------------------------------
Automated LLM model comparison for the MINAS systematic benchmark
(TCC-II-DRAFT.tex Section 5, RQ P6).

Iterates over a list of Ollama model tags, for each one:
  1. Recreates the orchestrator/cn-nssmf/ran-nssmf containers with that
     MINAS_MODEL (RAG_ENABLED and GUARDRAILS_ENABLED kept as passed).
  2. Waits for all three /health endpoints to be ready.
  3. Delegates to run_benchmark.py's run_one() loop for all intents.
  4. Writes per-(intent,repeat) JSONL for that model, same format as a
     manual run_benchmark.py invocation so results are interoperable.
  5. Prints a cross-model summary table at the end.

Prerequisites (same as run_benchmark.py):
  - Docker Compose reachable from this process (docker compose on PATH).
  - Ollama serving all requested models on the host (ollama pull <tag>
    before running this script).
  - Postgres published on localhost:5432.

Usage (from repo root):
    python agents/benchmark/run_llm_benchmark.py \\
        --models qwen2.5:7b llama3.1:8b \\
        --repeats 3

Optional flags:
    --rag-enabled true|false        (default: true)
    --guardrails-enabled true|false (default: true)
    --host http://localhost:8000
    --timeout 180
    --pause 2
    --health-timeout 120   seconds to wait for containers after recreate
    --out-dir agents/benchmark/results
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db_conn  # noqa: E402
from intent_set import INTENTS  # noqa: E402
from run_benchmark import print_summary, run_one  # noqa: E402

_AGENT_SERVICES = ["orchestrator", "cn-nssmf", "ran-nssmf"]
_HEALTH_PORTS   = {"orchestrator": 8000, "cn-nssmf": 8001, "ran-nssmf": 8002}


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------

def _recreate_agents(model: str, rag: str, guardrails: str) -> None:
    """Force-recreate the three agent containers with the given env vars."""
    env = {
        **os.environ,
        "MINAS_MODEL": model,
        "RAG_ENABLED": rag,
        "GUARDRAILS_ENABLED": guardrails,
    }
    cmd = [
        "docker", "compose", "up", "-d", "--force-recreate",
        *_AGENT_SERVICES,
    ]
    print(f"[llm-bench] recreating containers: MINAS_MODEL={model} "
          f"RAG_ENABLED={rag} GUARDRAILS_ENABLED={guardrails}")
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(f"[llm-bench] docker compose up failed (rc={result.returncode})")


def _wait_health(host: str, health_timeout: float) -> bool:
    """Poll /health on all three agents until all respond 200 or timeout."""
    urls = [f"http://localhost:{port}/health" for port in _HEALTH_PORTS.values()]
    deadline = time.monotonic() + health_timeout
    while time.monotonic() < deadline:
        try:
            ok = all(requests.get(u, timeout=5).status_code == 200 for u in urls)
            if ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(3)
    return False


# ---------------------------------------------------------------------------
# Per-model run
# ---------------------------------------------------------------------------

def _run_model(
    model: str,
    rag: str,
    guardrails: str,
    host: str,
    repeats: int,
    timeout: float,
    pause: float,
    out_dir: str,
    health_timeout: float,
    intent_ids: list[str] | None,
) -> list[dict]:
    _recreate_agents(model, rag, guardrails)

    print(f"[llm-bench] waiting for /health (up to {health_timeout}s) ...")
    if not _wait_health(host, health_timeout):
        print(f"[llm-bench] WARNING: containers not healthy after {health_timeout}s — "
              "proceeding anyway, results may contain HTTP errors")

    # condition label mirrors manual run_benchmark.py convention
    model_slug = model.replace(":", "-").replace("/", "-")
    condition  = f"{model_slug}_rag-{rag}_guardrails-{guardrails}"
    ts         = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    os.makedirs(out_dir, exist_ok=True)
    out_path   = os.path.join(out_dir, f"{condition}_{ts}.jsonl")

    intents = INTENTS
    if intent_ids:
        intents = [i for i in INTENTS if i["id"] in set(intent_ids)]

    conn    = get_db_conn()
    records: list[dict] = []
    total   = len(intents) * repeats
    done    = 0

    with open(out_path, "a", encoding="utf-8") as f:
        for intent in intents:
            for repeat in range(repeats):
                record = run_one(host, intent, repeat, conn, timeout)
                record["condition"] = condition
                record["model"]     = model
                records.append(record)
                f.write(json.dumps(record, default=str) + "\n")
                f.flush()
                done += 1
                print(f"  [{done}/{total}] {intent['id']} rep={repeat} "
                      f"success={record['task_success']} "
                      f"tools_ok={record['tool_selection_correct']} "
                      f"latency_s={record['decision_latency_s']} "
                      f"err={record['http_error']}")
                time.sleep(pause)

    print(f"[llm-bench] {model}: wrote {len(records)} records to {out_path}")
    return records


# ---------------------------------------------------------------------------
# Cross-model summary table
# ---------------------------------------------------------------------------

def _rate(records: list[dict], key: str) -> str:
    vals = [r[key] for r in records if r[key] is not None]
    if not vals:
        return "  n/a"
    rate = sum(1 for v in vals if v) / len(vals)
    return f"{rate:5.3f}"


def _mean(records: list[dict], key: str) -> str:
    vals = [r[key] for r in records if r[key] is not None]
    if not vals:
        return "   n/a"
    return f"{sum(vals) / len(vals):6.2f}"


def _print_cross_model_table(all_records: dict[str, list[dict]]) -> None:
    col_w = 24
    hdr = (
        f"{'Model':<{col_w}} "
        f"{'Task succ':>10} "
        f"{'Tool acc':>9} "
        f"{'Extr acc':>9} "
        f"{'Lat (s)':>8} "
        f"{'Guardrail rej':>14} "
        f"{'Redund calls':>13}"
    )
    sep = "-" * len(hdr)
    print(f"\n{'='*len(hdr)}")
    print("CROSS-MODEL SUMMARY")
    print(sep)
    print(hdr)
    print(sep)
    for model, records in all_records.items():
        n_gr  = sum(r["n_guardrail_rejections"] for r in records)
        n_red = sum(r["n_redundant_calls"] for r in records)
        print(
            f"{model:<{col_w}} "
            f"{_rate(records, 'task_success'):>10} "
            f"{_rate(records, 'tool_selection_correct'):>9} "
            f"{_rate(records, 'extraction_correct'):>9} "
            f"{_mean(records, 'decision_latency_s'):>8} "
            f"{n_gr:>14} "
            f"{n_red:>13}"
        )
    print(sep)
    print("(extraction_accuracy: valid intents only)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="MINAS LLM model comparison benchmark (TCC-II-DRAFT.tex Section 5 / P6)"
    )
    p.add_argument("--models", nargs="+", required=True,
                   help="Ollama model tags to compare, e.g. qwen2.5:7b llama3.1:8b")
    p.add_argument("--repeats", "-n", type=int, default=3,
                   help="Repeats per intent per model (Section 5.3)")
    p.add_argument("--rag-enabled", default="true", choices=["true", "false"],
                   help="RAG_ENABLED for all cells (default: true)")
    p.add_argument("--guardrails-enabled", default="true", choices=["true", "false"],
                   help="GUARDRAILS_ENABLED for all cells (default: true)")
    p.add_argument("--host", default="http://localhost:8000",
                   help="Orchestrator base URL")
    p.add_argument("--timeout", type=float, default=180.0,
                   help="HTTP timeout per /intent call, seconds")
    p.add_argument("--pause", type=float, default=2.0,
                   help="Seconds between /intent calls")
    p.add_argument("--health-timeout", type=float, default=120.0,
                   help="Seconds to wait for containers after recreate")
    p.add_argument("--ids", default=None,
                   help="Comma-separated intent ids to run (default: all)")
    p.add_argument("--out-dir", default=None,
                   help="Output directory for JSONL files "
                        "(default: agents/benchmark/results)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    out_dir = args.out_dir
    if out_dir is None:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

    intent_ids = args.ids.split(",") if args.ids else None

    all_records: dict[str, list[dict]] = {}
    for model in args.models:
        print(f"\n{'='*60}")
        print(f"[llm-bench] === MODEL: {model} ===")
        print(f"{'='*60}")
        records = _run_model(
            model=model,
            rag=args.rag_enabled,
            guardrails=args.guardrails_enabled,
            host=args.host,
            repeats=args.repeats,
            timeout=args.timeout,
            pause=args.pause,
            out_dir=out_dir,
            health_timeout=args.health_timeout,
            intent_ids=intent_ids,
        )
        all_records[model] = records
        print(f"\n--- per-model summary: {model} ---")
        print_summary(records)

    if len(all_records) > 1:
        _print_cross_model_table(all_records)


if __name__ == "__main__":
    main()
