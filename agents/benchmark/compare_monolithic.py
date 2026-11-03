"""
Monolithic baseline vs. MINAS (Qwen2.5 7B, RAG and guardrails on): pooled
rates with intent-level bootstrap intervals (same method as plots.py), the
paired difference per intent, UC1/UC2 split, latency and guardrail counts.

    python agents/benchmark/compare_monolithic.py
"""

from __future__ import annotations

import glob
import os
import statistics
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from plots import METRICS, load_rounds, metric  # noqa: E402

R = os.path.join(HERE, "results")
MULTI = "qwen2.5-7b_rag-true_guardrails-true"
MONO = "monolithic-qwen2.5-7b_rag-true_guardrails-true"


def paired_diff(a: list[dict], b: list[dict], sel, n_boot=5000, seed=0):
    """Mean over intents of (rate_b - rate_a), bootstrap over intents (pp)."""
    ra, rb = defaultdict(list), defaultdict(list)
    for r in a:
        if sel(r):
            ra[r["id"]].append(bool(r["task_success"]))
    for r in b:
        if sel(r):
            rb[r["id"]].append(bool(r["task_success"]))
    ids = sorted(set(ra) & set(rb))
    d = np.array([np.mean(rb[i]) - np.mean(ra[i]) for i in ids])
    rng = np.random.default_rng(seed)
    boots = d[rng.integers(0, len(d), size=(n_boot, len(d)))].mean(axis=1)
    return 100 * d.mean(), 100 * np.percentile(boots, 2.5), 100 * np.percentile(boots, 97.5)


def main() -> None:
    dirs = [os.path.join(R, "run2")] + sorted(glob.glob(os.path.join(R, "rounds", "r[0-9]*")))
    dirs += sorted(glob.glob(os.path.join(R, "monolithic", "r[0-9]*")))
    data = load_rounds(dirs)
    for cond in (MULTI, MONO):
        rounds = data.get(cond, [])
        print(f"\n== {cond}: {len(rounds)} rounds, {sum(len(r) for r in rounds)} executions")
        for name in METRICS:
            (p, lo, hi), per_round, (k, n) = metric(rounds, name)
            print(f"  {name:8} {p:5.1f} [{lo:4.0f}-{hi:4.0f}]  {k}/{n}  rounds={[round(x) for x in per_round]}")
        recs = [r for rs in rounds for r in rs]
        for uc in ("windowed_qos", "predictive_resource_adjustment"):
            v = [r for r in recs if r["validity"] == "valid" and r["use_case"] == uc]
            print(f"  valid {uc}: {sum(r['task_success'] for r in v)}/{len(v)}")
        lat = [r["wallclock_latency_s"] for r in recs if r.get("wallclock_latency_s") is not None]
        if lat:
            q = statistics.quantiles(lat, n=10)
            print(f"  latency median={statistics.median(lat):.1f}s p90={q[8]:.1f}s")
        print(f"  guardrail rejections={sum(r['n_guardrail_rejections'] for r in recs)}  "
              f"redundant>0 in {sum(1 for r in recs if r['n_redundant_calls'] > 0)}")
        fails = defaultdict(int)
        for r in recs:
            if r["validity"] == "valid" and not r["task_success"]:
                fails[r["id"]] += 1
        print(f"  valid failures by intent: {dict(fails)}")
    a = [r for rs in data.get(MULTI, []) for r in rs]
    b = [r for rs in data.get(MONO, []) for r in rs]
    if a and b:
        for label, sel in (("valid", lambda r: r["validity"] == "valid"),
                           ("valid UC1", lambda r: r["validity"] == "valid"
                            and r["use_case"] == "predictive_resource_adjustment"),
                           ("valid UC2", lambda r: r["validity"] == "valid" and r["use_case"] == "windowed_qos"),
                           ("invalid", lambda r: r["validity"] == "invalid")):
            m, lo, hi = paired_diff(a, b, sel)
            print(f"\npaired (mono - multi) {label}: {m:+.1f} pp [{lo:+.1f}, {hi:+.1f}]")


if __name__ == "__main__":
    main()
