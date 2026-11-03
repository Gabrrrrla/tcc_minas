"""
Benchmark of the monolithic baseline (agents/monolithic/main.py), same
protocol as the rounds of the multi-agent conditions: 16 intents x 3 repeats
per round, rounds independent, every record released after its execution.

Scoring reuses run_benchmark.run_one unchanged; only tool selection is
re-scored, because the monolithic agent calls the domain tools directly:
  cn_nssmf_apply_qos        -> configure_qos
  ran_nssmf_apply_resources -> allocate_prb

Prerequisites: the monolithic agent on :8010 (UC1/SLA monitors of the
multi-agent stack stopped, so no background LLM calls), Postgres on :5432.

    POSTGRES_HOST=localhost python agents/benchmark/run_monolithic.py --rounds 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from db import get_db_conn  # noqa: E402
from intent_set import INTENTS  # noqa: E402
from run_benchmark import release, run_one  # noqa: E402

ALIAS = {"cn_nssmf_apply_qos": "configure_qos", "ran_nssmf_apply_resources": "allocate_prb"}
CONDITION = "monolithic-qwen2.5-7b_rag-true_guardrails-true"


def rescore_tools(record: dict, intent: dict) -> None:
    called = set(record["tools_called_top_level"])
    req = {ALIAS.get(t, t) for t in intent["expected_tools_required"]}
    forb = {ALIAS.get(t, t) for t in intent["expected_tools_forbidden"]}
    record["tool_selection_correct"] = req.issubset(called) and not (forb & called)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="http://localhost:8010")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--first", type=int, default=1)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--pause", type=float, default=2.0)
    args = ap.parse_args()

    conn = get_db_conn()
    for rnd in range(args.first, args.first + args.rounds):
        out_dir = os.path.join(HERE, "results", "monolithic", f"r{rnd}")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, f"{CONDITION}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.jsonl")
        release(conn)
        n = 0
        with open(out, "a", encoding="utf-8") as f:
            for intent in INTENTS:
                for rep in range(args.repeats):
                    rec = run_one(args.host, intent, rep, conn, args.timeout)
                    rescore_tools(rec, intent)
                    rec["condition"] = CONDITION
                    rec["round"] = rnd
                    f.write(json.dumps(rec, default=str) + "\n")
                    f.flush()
                    n += 1
                    print(f"[r{rnd} {n}/{len(INTENTS) * args.repeats}] {intent['id']} rep={rep} "
                          f"success={rec['task_success']} tools_ok={rec['tool_selection_correct']} "
                          f"lat={rec['wallclock_latency_s']:.1f}s err={rec['http_error']}", flush=True)
                    time.sleep(args.pause)
        print(f"round {rnd} -> {out}", flush=True)


if __name__ == "__main__":
    main()
