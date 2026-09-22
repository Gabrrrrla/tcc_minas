"""
agents/benchmark/run_benchmark.py
-----------------------------------
Runner for the systematic benchmark (TCC-II-DRAFT.tex Section 5) — item 3 of
the benchmark critical path, after the RQ3 ablation switches
(agents/guardrails.py GUARDRAILS_ENABLED, agents/orchestrator/main.py
RAG_ENABLED) and the ground-truth workload (agents/benchmark/intent_set.py).

This script does NOT change MINAS_MODEL / RAG_ENABLED / GUARDRAILS_ENABLED
itself. Those are read once at container start (module import time), so
switching cells of the Section 5 factorial design (model x RAG x guardrails)
is an infrastructure step, not something a benchmark script should do to a
live stack on its own. The intended workflow for one cell is:

    1. Set the env vars for that cell and recreate the affected containers:
         MINAS_MODEL=llama3.1:8b RAG_ENABLED=false GUARDRAILS_ENABLED=true \
           docker compose up -d --force-recreate orchestrator cn-nssmf ran-nssmf
    2. Wait for :8000/:8001/:8002 /health, then label the run and point this
       script at the now-live stack:
         python agents/benchmark/run_benchmark.py \
           --condition llama3.1-8b_rag-off_guardrails-on --repeats 3

Each (intent, repeat) POSTs to the orchestrator's /intent endpoint and reads
back {"outcome", "trace", "intent_ids"} (agents/orchestrator/main.py run() —
extended in the same session this script was written, specifically so this
script would have something to read: the endpoint used to return only the
outcome text, which is not enough to compute any of the metrics below), then
cross-references the intents/policies/ran_allocations tables to compute the
metrics of Section 5.4. Requires network access to both the orchestrator
HTTP port and Postgres — against the default docker-compose.yml, running
this from the host machine works out of the box (both are published), no
override needed.

Metrics computed per (intent, repeat) — see Section 5.4 for definitions:
  task_success        bool   — reached 'applied' (valid intents), or
                                correctly avoided 'applied'/'degraded' with
                                the offending value in force (invalid ones)
  decision_latency_s  float  — DB-timestamp gap, intents.received_at to the
                                latest policies/ran_allocations.applied_at
  extraction_correct  bool   — recorded sst/target_thp_mbps/window vs ground
                                truth (valid intents only; None for invalid
                                ones, where "correct extraction" isn't the
                                question — see NOTE below)
  tool_selection_correct bool — top-level trace covers expected_tools_required
                                and avoids expected_tools_forbidden
  n_guardrail_rejections int  — guardrail:true results anywhere in the trace,
                                including nested domain-agent traces
  n_redundant_calls      int  — exact-repeat (tool, args) pairs, counted
                                separately per scope (orchestrator / cn-nssmf
                                / ran-nssmf), since agents/react.py's own
                                dedup cache still logs every attempt to trace
  reversion_status    str    — "not_due" / "reverted" / "overdue" (windowed
                                intents only; see NOTE below)

NOTE on invalid intents: MINAS's intent lifecycle has no dedicated "refused"
state (see intent_set.py's module docstring), so an invalid intent's
"correct" run may look like a normal one that simply never used the
offending value — extraction_correct against the OFFENDING ground-truth
value would then read as False even though the system behaved correctly.
That is why extraction_correct is not computed for invalid entries; only
task_success (did the bad value ever get committed) is.

NOTE on reversion: this script does not manufacture short test windows —
intent_set.py's windowed_qos entries carry fixed calendar dates so the same
static file also serves extraction-accuracy checks reproducibly. Run this
script again after an intent's window_end has actually elapsed (wall clock)
to get a "reverted"/"overdue" verdict; before that it reports "not_due",
which is not a failure.

Output: one JSON line per (intent, repeat) appended to --out (so partial
runs survive a crash), plus an aggregate summary printed at the end. Line
level detail is kept — n is small and Section 5.6 already flags high
variance, an aggregate-only log would hide it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

# make agents/ importable (shared db.py) whether run via `python
# agents/benchmark/run_benchmark.py` or `cd agents/benchmark && python run_benchmark.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db_conn  # noqa: E402
from intent_set import INTENTS  # noqa: E402

_THP_TOLERANCE_MBPS = 0.5
_WINDOW_TOLERANCE_SECONDS = 300  # LLM-generated ISO timestamps can round to the minute


# ---------------------------------------------------------------------------
# Trace helpers
# ---------------------------------------------------------------------------

def _flatten_trace(trace: list[dict], scope: str = "orchestrator"):
    """Yield (scope, tool, args, result) for every call in this trace,
    recursing into nested domain-agent traces: a cn_nssmf_*/ran_nssmf_* call's
    result embeds that agent's own ReAct trace under "trace", tagged with its
    own agent name under "agent" (see _run_directive in
    agents/cn-nssmf/main.py and agents/ran-nssmf/main.py)."""
    for entry in trace:
        result = entry.get("result")
        yield scope, entry["tool"], entry["input"], result
        if isinstance(result, dict) and isinstance(result.get("trace"), list):
            yield from _flatten_trace(result["trace"], result.get("agent", scope))


def _guardrail_rejections(flat: list[tuple]) -> list[dict]:
    return [
        {"scope": scope, "tool": tool, "args": args}
        for scope, tool, args, result in flat
        if isinstance(result, dict) and result.get("guardrail") is True
    ]


def _redundant_call_count(flat: list[tuple]) -> int:
    """Count exact-repeat (scope, tool, sorted-args) calls beyond the first —
    agents/react.py's dedup cache still appends every attempt to the trace,
    so this is directly countable without re-running anything."""
    from collections import Counter

    keys = [(scope, tool, json.dumps(args, sort_keys=True)) for scope, tool, args, _ in flat]
    counts = Counter(keys)
    return sum(c - 1 for c in counts.values() if c > 1)


# ---------------------------------------------------------------------------
# DB cross-reference
# ---------------------------------------------------------------------------

def _fetch_intent_record(conn, intent_id: int) -> dict | None:
    with conn, conn.cursor() as cur:
        cur.execute(
            "SELECT received_at, sst, target_thp_mbps, window_start, window_end, status "
            "FROM intents WHERE id = %s",
            (intent_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        received_at, sst, thp, window_start, window_end, status = row

        cur.execute(
            "SELECT applied_at, reverted_at FROM policies WHERE intent_id = %s ORDER BY applied_at",
            (intent_id,),
        )
        policies = cur.fetchall()

        cur.execute(
            "SELECT applied_at, reverted_at FROM ran_allocations WHERE intent_id = %s ORDER BY applied_at",
            (intent_id,),
        )
        ran_allocs = cur.fetchall()

    applied_ats = [p[0] for p in policies] + [r[0] for r in ran_allocs]
    decision_latency_s = (max(applied_ats) - received_at).total_seconds() if applied_ats else None

    reversion_status = None
    if window_end is not None:
        reverted_ats = [p[1] for p in policies if p[1]] + [r[1] for r in ran_allocs if r[1]]
        now = datetime.now(timezone.utc)
        if reverted_ats:
            reversion_status = "reverted"
        elif window_end <= now:
            reversion_status = "overdue"
        else:
            reversion_status = "not_due"

    return {
        "intent_id": intent_id,
        "received_at": received_at,
        "sst": sst,
        "target_thp_mbps": thp,
        "window_start": window_start,
        "window_end": window_end,
        "status": status,
        "decision_latency_s": decision_latency_s,
        "reversion_status": reversion_status,
        "n_policies": len(policies),
        "n_ran_allocations": len(ran_allocs),
    }


def _matches_expected_window(record: dict, expected: dict | None) -> bool:
    if expected is None:
        return record["window_start"] is None and record["window_end"] is None
    if record["window_start"] is None or record["window_end"] is None:
        return False
    exp_start = datetime.fromisoformat(expected["start"].replace("Z", "+00:00"))
    exp_end = datetime.fromisoformat(expected["end"].replace("Z", "+00:00"))
    return (
        abs((record["window_start"] - exp_start).total_seconds()) <= _WINDOW_TOLERANCE_SECONDS
        and abs((record["window_end"] - exp_end).total_seconds()) <= _WINDOW_TOLERANCE_SECONDS
    )


def _matches_ground_truth(record: dict, intent: dict) -> bool:
    if record["sst"] != intent["expected_sst"]:
        return False
    exp_thp = intent["expected_target_thp_mbps"]
    if exp_thp is None:
        if record["target_thp_mbps"] is not None:
            return False
    elif record["target_thp_mbps"] is None or abs(record["target_thp_mbps"] - exp_thp) > _THP_TOLERANCE_MBPS:
        return False
    return _matches_expected_window(record, intent["expected_window"])


def _offending_value_applied(record: dict, intent: dict) -> bool:
    """For an invalid intent: did this DB row commit the exact bad value the
    intent stated, and reach a status that means it's actually in force?"""
    if record["status"] not in ("applied", "degraded"):
        return False
    if record["sst"] != intent["expected_sst"]:
        return False
    exp_thp = intent["expected_target_thp_mbps"]
    if exp_thp is not None and (
        record["target_thp_mbps"] is None or abs(record["target_thp_mbps"] - exp_thp) > _THP_TOLERANCE_MBPS
    ):
        return False
    return True


# ---------------------------------------------------------------------------
# One (intent, repeat)
# ---------------------------------------------------------------------------

def run_one(host: str, intent: dict, repeat: int, conn, timeout: float) -> dict:
    t0 = datetime.now(timezone.utc)
    http_error = None
    body: dict = {}
    try:
        resp = requests.post(f"{host}/intent", json={"intent": intent["text"]}, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as exc:
        http_error = str(exc)
    t1 = datetime.now(timezone.utc)

    trace = body.get("trace", []) if isinstance(body, dict) else []
    intent_ids = body.get("intent_ids", []) if isinstance(body, dict) else []
    flat = list(_flatten_trace(trace))

    top_level_tools = {t["tool"] for t in trace}
    required = set(intent["expected_tools_required"])
    forbidden = set(intent["expected_tools_forbidden"])
    tool_selection_correct = required.issubset(top_level_tools) and not (forbidden & top_level_tools)

    db_records = [r for r in (_fetch_intent_record(conn, iid) for iid in intent_ids) if r is not None]

    extraction_correct = None
    reversion_status = None
    if intent["validity"] == "valid":
        extraction_correct = any(_matches_ground_truth(r, intent) for r in db_records) if db_records else False
        task_success = any(r["status"] == "applied" for r in db_records)
        due_statuses = [r["reversion_status"] for r in db_records if r["reversion_status"]]
        if due_statuses:
            reversion_status = "reverted" if "reverted" in due_statuses else (
                "overdue" if "overdue" in due_statuses else "not_due"
            )
    else:
        bad_applied = any(_offending_value_applied(r, intent) for r in db_records)
        task_success = not bad_applied

    return {
        "id": intent["id"],
        "repeat": repeat,
        "use_case": intent["use_case"],
        "phrasing": intent["phrasing"],
        "validity": intent["validity"],
        "text": intent["text"],
        "http_error": http_error,
        "wallclock_latency_s": (t1 - t0).total_seconds(),
        "created_intent_ids": intent_ids,
        "db_records": [
            {**r, "received_at": r["received_at"].isoformat(),
             "window_start": r["window_start"].isoformat() if r["window_start"] else None,
             "window_end": r["window_end"].isoformat() if r["window_end"] else None}
            for r in db_records
        ],
        "decision_latency_s": next((r["decision_latency_s"] for r in db_records if r["decision_latency_s"]), None),
        "n_guardrail_rejections": len(_guardrail_rejections(flat)),
        "guardrail_rejections": _guardrail_rejections(flat),
        "n_redundant_calls": _redundant_call_count(flat),
        "tools_called_top_level": sorted(top_level_tools),
        "tool_selection_correct": tool_selection_correct,
        "extraction_correct": extraction_correct,
        "task_success": task_success,
        "reversion_status": reversion_status,
        "final_text": body.get("outcome") if isinstance(body, dict) else None,
    }


# ---------------------------------------------------------------------------
# Aggregate + CLI
# ---------------------------------------------------------------------------

def _rate(records: list[dict], key: str) -> float | None:
    vals = [r[key] for r in records if r[key] is not None]
    return round(sum(1 for v in vals if v) / len(vals), 3) if vals else None


def print_summary(records: list[dict]) -> None:
    n = len(records)
    latencies = [r["decision_latency_s"] for r in records if r["decision_latency_s"] is not None]
    print(f"\n=== summary over {n} calls ===")
    print(f"task_success_rate       : {_rate(records, 'task_success')}")
    print(f"tool_selection_accuracy : {_rate(records, 'tool_selection_correct')}")
    print(f"extraction_accuracy     : {_rate(records, 'extraction_correct')} (valid intents only)")
    print(f"mean_decision_latency_s : {round(sum(latencies) / len(latencies), 2) if latencies else None}")
    print(f"total_guardrail_rejections : {sum(r['n_guardrail_rejections'] for r in records)}")
    print(f"total_redundant_calls      : {sum(r['n_redundant_calls'] for r in records)}")
    print(f"http_errors                : {sum(1 for r in records if r['http_error'])}")
    rev = [r["reversion_status"] for r in records if r["reversion_status"]]
    if rev:
        from collections import Counter
        print(f"reversion_status           : {dict(Counter(rev))}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="MINAS systematic benchmark runner (TCC-II-DRAFT.tex Section 5)")
    p.add_argument("--condition", required=True,
                   help="Label for this run's cell, e.g. qwen2.5-7b_rag-on_guardrails-on. "
                        "Purely descriptive — does not change any running container's config.")
    p.add_argument("--host", default="http://localhost:8000", help="Orchestrator base URL")
    p.add_argument("--repeats", "-n", type=int, default=3, help="Repeats per intent (Section 5.3)")
    p.add_argument("--ids", default=None, help="Comma-separated intent ids to run (default: all)")
    p.add_argument("--timeout", type=float, default=180.0, help="HTTP timeout per /intent call, seconds")
    p.add_argument("--pause", type=float, default=2.0, help="Seconds to sleep between calls")
    p.add_argument("--out", default=None, help="Output JSONL path (default: agents/benchmark/results/<condition>_<ts>.jsonl)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    intents = INTENTS
    if args.ids:
        wanted = set(args.ids.split(","))
        intents = [i for i in INTENTS if i["id"] in wanted]
        missing = wanted - {i["id"] for i in intents}
        if missing:
            sys.exit(f"unknown intent id(s): {sorted(missing)}")

    out_path = args.out
    if out_path is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{args.condition}_{ts}.jsonl")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)

    conn = get_db_conn()
    records: list[dict] = []
    total = len(intents) * args.repeats
    done = 0
    with open(out_path, "a", encoding="utf-8") as f:
        for intent in intents:
            for repeat in range(args.repeats):
                record = run_one(args.host, intent, repeat, conn, args.timeout)
                record["condition"] = args.condition
                records.append(record)
                f.write(json.dumps(record, default=str) + "\n")
                f.flush()
                done += 1
                print(f"[{done}/{total}] {intent['id']} rep={repeat} "
                      f"success={record['task_success']} tools_ok={record['tool_selection_correct']} "
                      f"guardrail_rej={record['n_guardrail_rejections']} "
                      f"latency_s={record['decision_latency_s']} "
                      f"err={record['http_error']}")
                time.sleep(args.pause)

    print(f"\nwrote {len(records)} records to {out_path}")
    print_summary(records)


if __name__ == "__main__":
    main()
