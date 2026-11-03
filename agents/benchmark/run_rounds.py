"""
agents/benchmark/run_rounds.py
------------------------------
Runs the agent benchmark as several independent ROUNDS, each one the full
6-condition design of Section 5 (3 models + 3 Qwen ablations, 16 intents x
`--repeats`), so results can be reported as pooled rates with per-round
variability instead of a single run.

    results/rounds/r1/<condition>_<ts>.jsonl   (6 files)
    results/rounds/r2/...

Each round shuffles the order of the conditions (seeded by the round number),
so no condition always runs first / at the same time of night. A round that
was interrupted is resumed: conditions whose JSONL is already complete are
skipped, incomplete files are moved to results/rounds/rN/incomplete/.

Prerequisites: same as run_llm_benchmark.py (containers built with the
current code, Ollama serving the 3 models, Postgres on localhost:5432).
Enforcement must stay mocked (ENFORCEMENT_MODE unset / "mock") so rounds stay
comparable with round 2.

Usage (from repo root, PowerShell or bash):
    python agents/benchmark/run_rounds.py --rounds 4 --first 1
    python agents/benchmark/run_rounds.py --rounds 2 --first 5   # later, r5..r6

Then:
    python agents/benchmark/plots.py --rounds agents/benchmark/results/run2 \
        agents/benchmark/results/rounds/r*
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
RUNNER = os.path.join(HERE, "run_llm_benchmark.py")
LOCK = os.path.join(HERE, "results", "rounds", ".lock")

sys.path.insert(0, HERE)
from intent_set import INTENTS  # noqa: E402

# (condition key written by run_llm_benchmark, model, rag, guardrails)
CONDITIONS = [
    ("qwen2.5-7b_rag-true_guardrails-true", "qwen2.5:7b", "true", "true"),
    ("llama3.1-8b_rag-true_guardrails-true", "llama3.1:8b", "true", "true"),
    ("otel-llm-e4b-it-latest_rag-true_guardrails-true", "otel-llm-e4b-it:latest", "true", "true"),
    ("qwen2.5-7b_rag-false_guardrails-true", "qwen2.5:7b", "false", "true"),
    ("qwen2.5-7b_rag-true_guardrails-false", "qwen2.5:7b", "true", "false"),
    ("qwen2.5-7b_rag-false_guardrails-false", "qwen2.5:7b", "false", "false"),
]


def log(msg: str) -> None:
    print(f"[rounds {datetime.now():%H:%M:%S}] {msg}", flush=True)


def other_benchmark_running() -> bool:
    """Two runners against the same containers corrupt each other
    (incident of 20/10, HANDOVER-2026-10-20 sec. 7)."""
    if sys.platform == "win32":
        cmd = ["powershell", "-NoProfile", "-Command",
               "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -match "
               "'run_llm_benchmark|run_benchmark.py' } | Measure-Object | "
               "Select-Object -ExpandProperty Count"]
        try:
            return int(subprocess.check_output(cmd, text=True).strip() or 0) > 0
        except Exception:
            return False
    out = subprocess.run(["pgrep", "-f", "run_llm_benchmark|run_benchmark.py"],
                         capture_output=True, text=True)
    return bool(out.stdout.strip())


def complete_file(round_dir: str, cond: str, expected: int) -> str | None:
    for p in sorted(glob.glob(os.path.join(round_dir, f"{cond}_*.jsonl"))):
        n = sum(1 for l in open(p, encoding="utf-8") if l.strip())
        if n >= expected:
            return p
        os.makedirs(os.path.join(round_dir, "incomplete"), exist_ok=True)
        shutil.move(p, os.path.join(round_dir, "incomplete", os.path.basename(p)))
        log(f"  moved incomplete {os.path.basename(p)} ({n}/{expected})")
    return None


def run_condition(round_dir: str, cond, repeats: int, extra: list[str]) -> int:
    key, model, rag, guard = cond
    logf = os.path.join(round_dir, f"{key}.log")
    cmd = [sys.executable, RUNNER, "--models", model, "--rag-enabled", rag,
           "--guardrails-enabled", guard, "--repeats", str(repeats),
           "--out-dir", round_dir, *extra]
    env = {**os.environ, "POSTGRES_HOST": os.environ.get("POSTGRES_HOST", "localhost"),
           "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    t0 = time.time()
    with open(logf, "a", encoding="utf-8") as f:
        rc = subprocess.call(cmd, cwd=REPO, env=env, stdout=f, stderr=subprocess.STDOUT)
    log(f"  {key}: rc={rc} in {(time.time() - t0) / 60:.1f} min")
    return rc


def summary(round_dir: str) -> None:
    for p in sorted(glob.glob(os.path.join(round_dir, "*.jsonl"))):
        rs = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
        v = [r for r in rs if r["validity"] == "valid"]
        i = [r for r in rs if r["validity"] == "invalid"]
        log(f"    {rs[0]['condition']:48} valid {sum(r['task_success'] for r in v)}/{len(v)}"
            f"  invalid {sum(r['task_success'] for r in i)}/{len(i)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds", type=int, required=True, help="how many rounds to run now")
    ap.add_argument("--first", type=int, default=1, help="index of the first round (r<first>)")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out-root", default=os.path.join(HERE, "results", "rounds"))
    ap.add_argument("--only", nargs="*", help="subset of condition keys (debug)")
    a, extra = ap.parse_known_args()

    if os.environ.get("ENFORCEMENT_MODE", "mock") != "mock":
        sys.exit("ENFORCEMENT_MODE must be 'mock' for benchmark rounds (comparability).")
    if other_benchmark_running():
        sys.exit("Another benchmark process is running; stop it first.")
    os.makedirs(a.out_root, exist_ok=True)
    if os.path.exists(LOCK):
        sys.exit(f"Lock file {LOCK} exists (another run_rounds?). Delete it if stale.")
    open(LOCK, "w").write(str(os.getpid()))

    expected = len(INTENTS) * a.repeats
    conds = [c for c in CONDITIONS if not a.only or c[0] in a.only]
    try:
        for r in range(a.first, a.first + a.rounds):
            round_dir = os.path.join(a.out_root, f"r{r}")
            os.makedirs(round_dir, exist_ok=True)
            order = conds[:]
            random.Random(r).shuffle(order)
            log(f"round r{r}: " + ", ".join(c[0].split("_")[0] + ("" if c[2:] == ("true", "true")
                else f"[rag={c[2]},gr={c[3]}]") for c in order))
            for cond in order:
                if complete_file(round_dir, cond[0], expected):
                    log(f"  {cond[0]}: already complete, skipped")
                    continue
                rc = run_condition(round_dir, cond, a.repeats, extra)
                if rc != 0 or not complete_file(round_dir, cond[0], expected):
                    log(f"  {cond[0]}: FAILED (rc={rc}); stopping so it can be resumed")
                    sys.exit(1)
            summary(round_dir)
    finally:
        # back to the default configuration (Qwen, RAG on, guardrails on)
        log("restoring default containers")
        subprocess.call([sys.executable, RUNNER, "--models", "qwen2.5:7b", "--repeats", "1",
                         "--ids", "wq-explicit-valid-1",
                         "--out-dir", os.path.join(a.out_root, "restore")],
                        cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        os.remove(LOCK)
    log("done")


if __name__ == "__main__":
    main()
