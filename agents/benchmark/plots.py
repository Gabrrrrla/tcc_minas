"""
agents/benchmark/plots.py
-------------------------
Figures for the Results chapter (TCC II, Section 6), replacing the tables.
Presentation mirrors ORION (arXiv:2603.03667, Sec. VI): one bar chart per
headline rate with error bars, a distribution plot (box + points) for
per-execution quantities, and a per-stage time breakdown.

Input: one or more ROUND directories, each holding the 6 condition JSONLs
written by run_llm_benchmark.py (e.g. results/run2, results/rounds/r3, ...).
Rates are pooled over all rounds (95% Wilson interval on the pooled count);
when there is more than one round, each round's own rate is drawn as a dot
over its bar, so round-to-round variability is visible.

Usage (from repo root):
    python agents/benchmark/plots.py \
        --rounds agents/benchmark/results/run2 agents/benchmark/results/rounds/r* \
        --probes agents/benchmark/results/window-probes_20260928T025918Z.jsonl \
        --rq4    agents/benchmark/results/rq4_minas_synth_20260928T012326Z.jsonl \
        --out    tcc2/figures/results

Writes one PDF per figure (vector, for \\includegraphics) plus a PNG preview,
and prints the numbers behind each figure so the text can quote them.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from intent_set import INTENTS  # noqa: E402

# ---------------------------------------------------------------------------
# Style: same colour rule as the architecture figures (HANDOVER-2026-10-12):
# blue = MINAS / LLM path, greys = everything else. Readable in B&W.
# ---------------------------------------------------------------------------
BLUE = "#1F5FA0"
BLUE_L = "#8FB3D9"
GREY_D = "#4D4D4D"
GREY = "#9A9A9A"
GREY_L = "#D9D9D9"

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 9,
    "axes.titlesize": 9,
    "axes.labelsize": 9,
    "legend.fontsize": 8,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "axes.grid.axis": "y",
    "grid.color": "#E5E5E5",
    "grid.linewidth": 0.6,
    "axes.axisbelow": True,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.02,
    "pdf.fonttype": 42,
})
FULL_W = 6.3   # \textwidth of the template, inches
HALF_H = 2.4

# condition key (as written in the JSONL) -> (label, group)
CONDITIONS = [
    ("qwen2.5-7b_rag-true_guardrails-true", "Qwen2.5 7B", "model"),
    ("llama3.1-8b_rag-true_guardrails-true", "Llama 3.1 8B", "model"),
    ("otel-llm-e4b-it-latest_rag-true_guardrails-true", "OTel-LLM\nE4B-IT", "model"),
    ("qwen2.5-7b_rag-false_guardrails-true", "Qwen\nno RAG", "ablation"),
    ("qwen2.5-7b_rag-true_guardrails-false", "Qwen\nno guardr.", "ablation"),
    ("qwen2.5-7b_rag-false_guardrails-false", "Qwen\nneither", "ablation"),
]
INTENT_BY_ID = {i["id"]: i for i in INTENTS}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n == 0:
        return (math.nan, math.nan, math.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * p, 100 * max(0.0, c - h), 100 * min(1.0, c + h)


def load_rounds(dirs: list[str]) -> dict[str, list[list[dict]]]:
    """condition -> [records of round 0, records of round 1, ...]"""
    out: dict[str, list[list[dict]]] = defaultdict(list)
    for d in dirs:
        paths = sorted(glob.glob(os.path.join(d, "*.jsonl")))
        if not paths:
            print(f"[plots] warning: no JSONL in {d}", file=sys.stderr)
        for p in paths:
            recs = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
            if recs:
                out[recs[0]["condition"]].append(recs)
    return out


def rate(recs: list[dict], pred, sel=lambda r: True) -> tuple[int, int]:
    sub = [r for r in recs if sel(r)]
    return sum(1 for r in sub if pred(r)), len(sub)


METRICS = {
    "valid": (lambda r: r["task_success"], lambda r: r["validity"] == "valid"),
    "invalid": (lambda r: r["task_success"], lambda r: r["validity"] == "invalid"),
    "toolsel": (lambda r: bool(r["tool_selection_correct"]), lambda r: True),
    "extract": (lambda r: bool(r["extraction_correct"]),
                lambda r: r["validity"] == "valid" and r["extraction_correct"] is not None),
}


def cluster_bootstrap(recs: list[dict], pred, n_boot: int = 5000, seed: int = 0):
    """95% percentile interval resampling INTENTS (with all their executions),
    not executions: the executions of one intent are not independent, and the
    population the results generalize to is intents. Returns (lo, hi) in %."""
    per: dict[str, list[bool]] = defaultdict(list)
    for r in recs:
        per[r["id"]].append(bool(pred(r)))
    ids = sorted(per)
    if not ids:
        return math.nan, math.nan
    rng = np.random.default_rng(seed)
    k = np.array([sum(per[i]) for i in ids], dtype=float)
    n = np.array([len(per[i]) for i in ids], dtype=float)
    idx = rng.integers(0, len(ids), size=(n_boot, len(ids)))
    p = k[idx].sum(axis=1) / n[idx].sum(axis=1)
    return 100 * float(np.percentile(p, 2.5)), 100 * float(np.percentile(p, 97.5))


def metric(rounds: list[list[dict]], name: str):
    """Pooled rate with a 95% intent-level bootstrap interval (p, lo, hi),
    the per-round rates, and (k, n)."""
    pred, sel = METRICS[name]
    per_round = [rate(rs, pred, sel) for rs in rounds]
    k = sum(a for a, _ in per_round)
    n = sum(b for _, b in per_round)
    lo, hi = cluster_bootstrap([r for rs in rounds for r in rs if sel(r)], pred)
    p = 100 * k / n if n else math.nan
    return (p, lo, hi), [100 * a / b for a, b in per_round if b], (k, n)


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def _xticks(ax, labels):
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels)
    # dashed separator between the model comparison and the ablations
    n_models = sum(1 for _, _, g in CONDITIONS if g == "model")
    ax.axvline(n_models - 0.5, color=GREY, lw=0.8, ls="--")
    ymax = ax.get_ylim()[1]
    ax.text(n_models / 2 - 0.5, ymax, "model comparison", ha="center", va="bottom",
            fontsize=7.5, color=GREY_D)
    ax.text(n_models + (len(labels) - n_models) / 2 - 0.5, ymax, "ablation (Qwen2.5 7B)",
            ha="center", va="bottom", fontsize=7.5, color=GREY_D)


def grouped_rates(ax, data, names, legend, colors, hatches):
    conds = [c for c in CONDITIONS if c[0] in data]
    n = len(names)
    width = 0.8 / n
    for j, name in enumerate(names):
        for i, (key, _, _) in enumerate(conds):
            (p, lo, hi), per_round, _ = metric(data[key], name)
            x = i + (j - (n - 1) / 2) * width
            ax.bar(x, p, width * 0.92, color=colors[j], hatch=hatches[j],
                   edgecolor=GREY_D, lw=0.5, label=legend[j] if i == 0 else None)
            ax.errorbar(x, p, yerr=[[p - lo], [hi - p]], fmt="none",
                        ecolor="black", elinewidth=0.8, capsize=2)
            if len(per_round) > 1:
                jitter = np.linspace(-width * 0.25, width * 0.25, len(per_round))
                ax.scatter(x + jitter, per_round, s=7, color="black", zorder=3, lw=0)
            ax.text(x, 2, f"{p:.0f}", ha="center", va="bottom", fontsize=6.5,
                    color="white" if colors[j] in (BLUE, GREY_D) and p > 12 else "black")
    ax.set_ylim(0, 108)
    ax.set_ylabel("Executions (%)")
    _xticks(ax, [lbl for _, lbl, _ in conds])
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), frameon=False, ncol=1)


def save(fig, out, name):
    os.makedirs(out, exist_ok=True)
    fig.savefig(os.path.join(out, f"{name}.pdf"))
    fig.savefig(os.path.join(out, f"{name}.png"), dpi=200)
    plt.close(fig)
    print(f"[plots] wrote {name}.pdf")


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def fig_success(data, out):
    """Task success, valid vs invalid intents (ORION Fig. 7 analogue)."""
    fig, ax = plt.subplots(figsize=(FULL_W, HALF_H))
    grouped_rates(ax, data, ["valid", "invalid"],
                  ["Valid intents", "Invalid intents"], [BLUE, GREY_L], ["", "///"])
    save(fig, out, "fig-bench-success")


def fig_toolsel(data, out):
    """Tool-selection and extraction accuracy (ORION Fig. 9 analogue)."""
    fig, ax = plt.subplots(figsize=(FULL_W, HALF_H))
    grouped_rates(ax, data, ["toolsel", "extract"],
                  ["Tool selection", "Parameter extraction"], [BLUE_L, GREY], ["", ""])
    save(fig, out, "fig-bench-toolsel")


def _distribution(ax, data, field, log=False):
    conds = [c for c in CONDITIONS if c[0] in data]
    series = [[r[field] for rs in data[k] for r in rs if r.get(field) is not None]
              for k, _, _ in conds]
    bp = ax.boxplot(series, positions=range(len(conds)), widths=0.5, showfliers=False,
                    patch_artist=True, medianprops={"color": "black", "lw": 1.2})
    for patch, (_, _, g) in zip(bp["boxes"], conds):
        patch.set_facecolor(BLUE_L if g == "model" else GREY_L)
        patch.set_edgecolor(GREY_D)
        patch.set_linewidth(0.6)
    rng = np.random.default_rng(0)
    for i, s in enumerate(series):
        ax.scatter(i + rng.uniform(-0.18, 0.18, len(s)), s, s=4, color=GREY_D,
                   alpha=0.55, lw=0, zorder=3)
    if log:
        ax.set_yscale("symlog" if any(v == 0 for s in series for v in s) else "log")
    return conds, series


def fig_latency(data, out):
    """End-to-end latency per execution (ORION Fig. 10 analogue: distribution)."""
    fig, ax = plt.subplots(figsize=(FULL_W, HALF_H))
    conds, series = _distribution(ax, data, "wallclock_latency_s", log=True)
    ax.set_ylabel("Latency per intent (s, log)")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter("%g"))
    _xticks(ax, [lbl for _, lbl, _ in conds])
    save(fig, out, "fig-bench-latency")
    for (_, lbl, _), s in zip(conds, series):
        print(f"    latency {lbl.replace(chr(10), ' '):18} median={statistics.median(s):.1f}s "
              f"p90={np.percentile(s, 90):.1f}s max={max(s):.1f}s n={len(s)}")


def fig_redundant(data, out):
    """Redundant calls per execution: median and outliers, not just the total."""
    fig, ax = plt.subplots(figsize=(FULL_W, HALF_H))
    conds, series = _distribution(ax, data, "n_redundant_calls", log=True)
    ax.set_ylabel("Redundant calls per intent")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter("%g"))
    _xticks(ax, [lbl for _, lbl, _ in conds])
    save(fig, out, "fig-bench-redundant")
    for (_, lbl, _), s in zip(conds, series):
        nz = sum(1 for v in s if v)
        print(f"    redundant {lbl.replace(chr(10), ' '):18} median={statistics.median(s):g} "
              f"max={max(s)} total={sum(s)} executions_with_any={nz}/{len(s)}")


def fig_uc1_status(data, out):
    """Final status of the valid predictive (UC1) intents, stacked."""
    order = ["degraded", "applied", "scheduled", "failed", "not recorded"]
    colors = {"degraded": BLUE, "applied": BLUE_L, "scheduled": GREY_L,
              "failed": GREY_D, "not recorded": "white"}
    hatches = {"scheduled": "///", "not recorded": "..."}
    conds = [c for c in CONDITIONS if c[0] in data]
    fig, ax = plt.subplots(figsize=(FULL_W, HALF_H))
    for i, (key, _, _) in enumerate(conds):
        cnt: Counter = Counter()
        n = 0
        for rs in data[key]:
            for r in rs:
                if r["use_case"] != "predictive_resource_adjustment" or r["validity"] != "valid":
                    continue
                n += 1
                # no row = the model never called record_intent
                st = r["db_records"][-1]["status"] if r["db_records"] else "not recorded"
                cnt[st if st in order else "not recorded"] += 1
        bottom = 0.0
        for st in order:
            v = 100 * cnt[st] / n if n else 0
            if v:
                ax.bar(i, v, 0.6, bottom=bottom, color=colors[st], hatch=hatches.get(st, ""),
                       edgecolor=GREY_D, lw=0.5, label=st)
                bottom += v
        print(f"    UC1 status {key:48} n={n} {dict(cnt)}")
    h, l = ax.get_legend_handles_labels()
    uniq = dict(zip(l, h))
    ax.legend([uniq[k] for k in order if k in uniq], [k for k in order if k in uniq],
              loc="upper left", bbox_to_anchor=(1.0, 1.0), frameon=False)
    ax.set_ylim(0, 108)
    ax.set_ylabel("Valid UC1 executions (%)")
    _xticks(ax, [lbl for _, lbl, _ in conds])
    save(fig, out, "fig-bench-uc1-status")


def fig_leaks(data, out):
    """Invalid intents whose offending value went into force, by kind."""
    kinds = [("non-positive", "Throughput $\\leq$ 0"), ("unsupported SST", "Unsupported SST"),
             ("window_end", "Reversed window")]
    colors = [BLUE, GREY, GREY_L]
    hatches = ["", "", "///"]
    conds = [c for c in CONDITIONS if c[0] in data]
    fig, ax = plt.subplots(figsize=(FULL_W, HALF_H))
    for i, (key, _, _) in enumerate(conds):
        n_inv = sum(1 for rs in data[key] for r in rs if r["validity"] == "invalid")
        cnt = Counter()
        for rs in data[key]:
            for r in rs:
                if r["validity"] == "invalid" and not r["task_success"]:
                    reason = INTENT_BY_ID[r["id"]]["invalid_reason"]
                    cnt[next(k for k, _ in kinds if k in reason)] += 1
        bottom = 0.0
        for (k, lbl), c, h in zip(kinds, colors, hatches):
            v = 100 * cnt[k] / n_inv if n_inv else 0
            ax.bar(i, v, 0.6, bottom=bottom, color=c, hatch=h, edgecolor=GREY_D, lw=0.5,
                   label=lbl if i == 0 else None)
            bottom += v
        ax.text(i, bottom + 1.5, f"{bottom:.0f}%", ha="center", fontsize=7)
        print(f"    leaks {key:48} n_invalid={n_inv} {dict(cnt)}")
    ax.set_ylim(0, 100)
    ax.set_ylabel("Invalid executions with\noffending value in force (%)")
    ax.legend(loc="upper left", frameon=False)
    _xticks(ax, [lbl for _, lbl, _ in conds])
    save(fig, out, "fig-bench-leaks")


def fig_rounds(data, out):
    """Valid-intent success per round, one line per condition: stability."""
    if max(len(v) for v in data.values()) < 2:
        print("[plots] fig-bench-rounds skipped (needs >= 2 rounds)")
        return
    fig, ax = plt.subplots(figsize=(FULL_W, HALF_H))
    styles = [("-", "o", BLUE), ("-", "s", GREY_D), ("-", "^", GREY),
              ("--", "o", BLUE_L), ("--", "s", GREY), ("--", "^", GREY_L)]
    for (key, lbl, _), (ls, mk, c) in zip(CONDITIONS, styles):
        if key not in data:
            continue
        _, per_round, _ = metric(data[key], "valid")
        ax.plot(range(1, len(per_round) + 1), per_round, ls=ls, marker=mk, color=c,
                ms=4, lw=1, label=lbl.replace("\n", " "))
        print(f"    rounds {lbl.replace(chr(10), ' '):18} valid per round={['%.0f' % v for v in per_round]} "
              f"mean={statistics.mean(per_round):.1f} sd={statistics.pstdev(per_round):.1f}")
    ax.set_xlabel("Round")
    ax.set_ylabel("Success, valid intents (%)")
    ax.set_ylim(-3, 103)
    ax.set_xticks(range(1, max(len(v) for v in data.values()) + 1))
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), frameon=False)
    save(fig, out, "fig-bench-rounds")


def fig_probes(paths, out):
    """Window probes: activation (core, RAN) and reversion delays (ORION Fig. 12).
    With several JSONL files (repetitions), one row per target with one dot per
    repetition; with one file, the original bar layout."""
    files = [p for pat in paths for p in sorted(glob.glob(pat))]
    if len(files) > 1:
        return fig_probes_reps(files, out)
    rs = [json.loads(l) for l in open(files[0], encoding="utf-8") if l.strip()]
    rs.sort(key=lambda r: r["target_thp_mbps"])
    labels = [f"{r['target_thp_mbps']:g} Mbps\n({r['activated_status']})" for r in rs]
    cn = [r["cn_activation_delay_s"] for r in rs]
    ran_extra = [max(0.0, r["ran_activation_delay_s"] - r["cn_activation_delay_s"]) for r in rs]
    rev = [r["reversion_delay_s"] for r in rs]
    y = np.arange(len(rs))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(FULL_W, 2.1), sharey=True,
                                 gridspec_kw={"width_ratios": [3, 2]})
    a1.barh(y, cn, 0.55, color=BLUE, edgecolor=GREY_D, lw=0.5, label="Core configured")
    a1.barh(y, ran_extra, 0.55, left=cn, color=BLUE_L, edgecolor=GREY_D, lw=0.5,
            label="+ RAN configured")
    a1.set_title("Activation (LLM path)")
    a1.set_xlabel("Seconds")
    a1.legend(loc="upper center", bbox_to_anchor=(0.5, -0.32), ncol=2, frameon=False)
    a2.barh(y, rev, 0.55, color=GREY_L, edgecolor=GREY_D, lw=0.5)
    a2.axvline(15, color=GREY_D, ls="--", lw=0.8)
    a2.text(14.7, -0.45, "scheduler\npoll", fontsize=7, ha="right", va="bottom", color=GREY_D)
    a2.set_title("Reversion (deterministic)")
    a2.set_xlabel("Seconds")
    for a in (a1, a2):
        a.grid(axis="x", color="#E5E5E5", lw=0.6)
        a.grid(axis="y", visible=False)
    a1.set_yticks(y)
    a1.set_yticklabels(labels)
    save(fig, out, "fig-probes")


def fig_probes_reps(files, out):
    rs = []
    for i, f in enumerate(files):
        for l in open(f, encoding="utf-8"):
            if l.strip():
                r = json.loads(l); r["_rep"] = i; rs.append(r)
    rs = [r for r in rs if r.get("target_thp_mbps") is not None]
    targets = sorted({r["target_thp_mbps"] for r in rs})
    labels = []
    for t in targets:
        st = Counter(r.get("activated_status") or "none" for r in rs if r["target_thp_mbps"] == t)
        labels.append(f"{t:g} Mbps\n" + ", ".join(f"{k} {v}" for k, v in sorted(st.items())))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(FULL_W, 2.4), sharey=True,
                                 gridspec_kw={"width_ratios": [3, 2]})
    jit = {0: -0.15, 1: 0.0, 2: 0.15}
    seen = set()
    for r in rs:
        y = targets.index(r["target_thp_mbps"])
        yy = y + jit[r["_rep"] % 3]
        cn, ran = r.get("cn_activation_delay_s"), r.get("ran_activation_delay_s")
        if cn is not None:
            a1.plot(cn, yy, "o", ms=4.5, color=BLUE, mec=GREY_D, mew=0.4,
                    label=None if "c" in seen else "Core configured"); seen.add("c")
        if ran is not None:
            a1.plot(ran, yy, "s", ms=4.5, color=BLUE_L, mec=GREY_D, mew=0.4,
                    label=None if "r" in seen else "RAN configured"); seen.add("r")
        if cn is not None and ran is not None:
            a1.plot([cn, ran], [yy, yy], color=GREY_L, lw=0.8, zorder=0)
        if r.get("reversion_delay_s") is not None and r.get("final_status") == "reverted":
            a2.plot(r["reversion_delay_s"], yy, "o", ms=4.5, color=GREY_L, mec=GREY_D, mew=0.4)
        if r.get("activated_status") in ("failed", None):
            a1.plot(0.8, yy, "x", ms=5, color="#B23A3A",
                    label=None if "f" in seen else "Activation failed"); seen.add("f")
    a1.set_title("Activation (LLM path)")
    a1.set_xlabel("Seconds after window start")
    a1.legend(loc="upper center", bbox_to_anchor=(0.5, -0.3), ncol=3, frameon=False)
    a2.axvline(15, color=GREY_D, ls="--", lw=0.8)
    a2.set_title("Reversion (deterministic)")
    a2.set_xlabel("Seconds after window end")
    for a in (a1, a2):
        a.grid(axis="x", color="#E5E5E5", lw=0.6)
        a.grid(axis="y", visible=False)
        a.set_xlim(left=0)
    a1.set_yticks(range(len(targets)))
    a1.set_yticklabels(labels, fontsize=7)
    save(fig, out, "fig-probes")


def fig_rq4(path, out):
    rs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    models = [("baseline_train_mean", "Train. mean"), ("baseline_persistence", "Persistence"),
              ("random_forest", "RandomForest"), ("gradient_boosting", "Grad. boost."),
              ("lstm", "LSTM")]
    fig, axes = plt.subplots(1, 2, figsize=(FULL_W, 2.1), sharey=False)
    for ax, sst, title in zip(axes, (1, 2), ("SST=1 (eMBB)", "SST=2 (URLLC)")):
        by = {r["model"]: r for r in rs if r["sst"] == sst}
        x = np.arange(len(models))
        mae = [by[m]["mae"] for m, _ in models]
        rmse = [by[m]["rmse"] for m, _ in models]
        ax.bar(x - 0.19, mae, 0.36, color=BLUE, edgecolor=GREY_D, lw=0.5, label="MAE")
        ax.bar(x + 0.19, rmse, 0.36, color=GREY_L, hatch="///", edgecolor=GREY_D, lw=0.5,
               label="RMSE")
        ax.axhline(by["baseline_persistence"]["mae"], color=BLUE, ls=":", lw=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels([l for _, l in models], rotation=25, ha="right")
        ax.set_title(title)
    axes[0].set_ylabel("Error (Mbps)")
    axes[0].legend(frameon=False)
    save(fig, out, "fig-rq4")


def fig_enforcement(path, out):
    """Lab: measured slice throughput across an enforced window."""
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    life = next(r for r in rows if r["kind"] == "lifecycle")
    smp = [r for r in rows if r["kind"] == "sample"]
    sst = str(life["sst"])
    dur = (datetime.fromisoformat(life["window_end"]) -
           datetime.fromisoformat(life["window_start"])).total_seconds()
    fig, ax = plt.subplots(figsize=(FULL_W, 2.3))
    ax.axvspan(0, dur, color=GREY_L, alpha=0.6, lw=0, label="Intent window")
    for k, c, ls in (("1", BLUE, "-"), ("2", GREY_D, "--")):
        xs = [s["rel_s"] for s in smp if s["thp_dl_mbps"].get(k) is not None]
        ys = [s["thp_dl_mbps"][k] for s in smp if s["thp_dl_mbps"].get(k) is not None]
        if xs and any(ys):
            ax.plot(xs, ys, ls=ls, color=c, lw=1.2, label=f"SST={k} measured DL")
    ax.hlines(life["target_thp_mbps"], 0, dur, color=BLUE, ls=":", lw=1.2,
              label=f"Intent target ({life['target_thp_mbps']:g} Mbps, SST={sst})")
    ax.set_ylim(0, ax.get_ylim()[1] * 1.15)
    for x, lbl in ((life.get("activation_delay_s"), "both domains\nconfigured"),
                   (dur + (life.get("reversion_delay_s") or 0), "reverted")):
        if x is not None:
            ax.axvline(x, color=GREY_D, lw=0.8)
            ax.text(x, ax.get_ylim()[1], " " + lbl, fontsize=7, va="top", color=GREY_D)
    ax.set_xlabel("Time relative to window start (s)")
    ax.set_ylabel("Throughput (Mbps)")
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), frameon=False)
    save(fig, out, "fig-enforcement")


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds", nargs="+", default=[], help="round directories (6 JSONLs each)")
    ap.add_argument("--probes", nargs="+", help="window-probes JSONL file(s)/globs; several = repetitions")
    ap.add_argument("--rq4", help="rq4 JSONL")
    ap.add_argument("--enforcement", help="enforcement-probe JSONL (lab)")
    ap.add_argument("--out", default="tcc2/figures/results")
    a = ap.parse_args()

    dirs = [d for pat in a.rounds for d in sorted(glob.glob(pat)) if os.path.isdir(d)]
    if not dirs:  # e.g. only the lab figure: --enforcement <file>
        if a.probes:
            fig_probes(a.probes, a.out)
        if a.rq4:
            fig_rq4(a.rq4, a.out)
        if a.enforcement:
            fig_enforcement(a.enforcement, a.out)
        return
    data = load_rounds(dirs)
    print(f"[plots] {len(dirs)} round(s): " + ", ".join(dirs))
    for key, lbl, _ in CONDITIONS:
        if key in data:
            print(f"  {lbl.replace(chr(10), ' '):18} rounds={len(data[key])} "
                  f"executions={sum(len(r) for r in data[key])}")
            for m in METRICS:
                (p, lo, hi), _, (k, n) = metric(data[key], m)
                print(f"    {m:8} {p:5.1f} [{lo:.0f}-{hi:.0f}]  ({k}/{n})")

    fig_success(data, a.out)
    fig_toolsel(data, a.out)
    fig_latency(data, a.out)
    fig_redundant(data, a.out)
    fig_uc1_status(data, a.out)
    fig_leaks(data, a.out)
    fig_rounds(data, a.out)
    if a.probes:
        fig_probes(a.probes, a.out)
    if a.rq4:
        fig_rq4(a.rq4, a.out)
    if a.enforcement:
        fig_enforcement(a.enforcement, a.out)


if __name__ == "__main__":
    main()
