"""
agents/benchmark/lab_analysis.py
--------------------------------
Numbers and figures of the laboratory sessions (09 and 10/11/2026, lab-data/).

Ground truth for radio throughput is the gNB console trace (gnb/gnb.stdout):
one table row per UE (RNTI) per second, with the DL bitrate the gNB actually
scheduled. The UPF counters (prometheus/*.json and the probes' thp_dl_mbps)
count what entered the slice's tunnel, not what reached the UE, and on
09/11 they show impossible values (1.6-5.6 Gbps, ogstun == ogstun2), so they
are not used for radio throughput here.

The trace has no timestamps and restarts with every gNB restart, so it only
covers the time between the last restart and the collection.

    python agents/benchmark/lab_analysis.py              # prints the numbers
    python agents/benchmark/lab_analysis.py --figs tcc2/figures
    python agents/benchmark/lab_analysis.py --e2 lab-data/<run> [...] --figs <dir>
        E2 with saturation (LAB-2026-11-13-saturacao.md): per-UE medians before,
        during and after the PRB reservation, and the restart gaps, from the
        timestamped gNB consoles of the run.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics as st
from datetime import datetime

try:  # optional: on the lab server the numbers are enough, figures come later
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: E402
except ImportError:  # pragma: no cover
    matplotlib = plt = None

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LAB = os.path.join(REPO, "lab-data")
# third session (18/11): copied as delivered, outside lab-data/
LAB3 = os.path.join(REPO, "lab-data-2026-11-18", "lab-data-2026-11-18")

# same palette as plots.py
BLUE = "#1F5FA0"
BLUE_L = "#8FB3D9"
GREY_D = "#4D4D4D"
GREY = "#9A9A9A"
GREY_L = "#D9D9D9"
FULL_W = 6.3
if plt is not None:
  plt.rcParams.update({
    "font.family": "serif", "font.size": 9, "axes.titlesize": 9, "axes.labelsize": 9,
    "legend.fontsize": 8, "xtick.labelsize": 8, "ytick.labelsize": 8,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "axes.grid.axis": "y", "grid.color": "#E5E5E5", "grid.linewidth": 0.6,
    "axes.axisbelow": True, "savefig.bbox": "tight", "savefig.pad_inches": 0.02,
    "pdf.fonttype": 42,
  })

# optional epoch prefix written by gnb_restart.sh ("1794255338.269   1 4652 | ...")
ROW = re.compile(r"^(?:(\d{10}\.\d+)\s+)?\s*\d+\s+([0-9a-f]{4})\s+\|")
UNIT = {"k": 1e-3, "M": 1.0, "G": 1e3}


def mbps(tok: str) -> float:
    if tok in ("0", "n/a"):
        return 0.0
    if tok[-1] in UNIT:
        return float(tok[:-1]) * UNIT[tok[-1]]
    return float(tok) * 1e-6


def gnb_trace(path: str) -> list[dict[str, float]]:
    """One dict {rnti: DL Mbps} per second. A new second starts when an RNTI repeats."""
    ticks, cur = [], {}
    for line in open(path, encoding="utf-8", errors="ignore"):
        m = ROW.match(line)
        if not m:
            continue
        p = line.split()[1:] if m.group(1) else line.split()
        try:
            v = mbps(p[6])
        except (ValueError, IndexError):
            continue
        r = m.group(2)
        if r in cur:
            ticks.append(cur)
            cur = {}
        cur[r] = v
    if cur:
        ticks.append(cur)
    return ticks


def active_series(ticks, min_mbps=50.0):
    """Per-RNTI series (same length as ticks) for the RNTIs that carried traffic."""
    rn = sorted({r for t in ticks for r, v in t.items() if v >= min_mbps})
    return {r: [t.get(r, 0.0) for t in ticks] for r in rn}


def describe(vals):
    vals = [v for v in vals if v > 50]  # seconds with traffic
    if len(vals) < 2:
        return None
    q = st.quantiles(vals, n=20)
    return dict(n=len(vals), mean=st.mean(vals), median=st.median(vals), p5=q[0], p95=q[-1],
                max=max(vals))


def read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def ts(s: str) -> datetime:
    s = s.replace(" ", "T")
    if s.endswith("+00"):
        s += ":00"
    return datetime.fromisoformat(s)


def lifecycle_day2(db=None):
    """Intents 10-12 (10/11), or 13-15 (18/11) with db=LAB3 dump: window,
    core/RAN configuration and reversion instants."""
    db = db or os.path.join(LAB, "20261110-1616_dia-completo", "db")
    intents = {r["id"]: r for r in read_csv(os.path.join(db, "intents.csv"))}
    pol = {r["intent_id"]: r for r in read_csv(os.path.join(db, "policies.csv"))}
    ran = {r["intent_id"]: r for r in read_csv(os.path.join(db, "ran_allocations.csv"))}
    out = []
    for iid in sorted(pol, key=int):
        it, p, a = intents[iid], pol[iid], ran[iid]
        w0, w1 = ts(it["window_start"]), ts(it["window_end"])
        rel = lambda x: (ts(x) - w0).total_seconds()  # noqa: E731
        out.append(dict(
            intent=int(iid), target=float(it["target_thp_mbps"]), prb=int(a["prb_allocated"]),
            supported=float(a["supported_thp_mbps"]), status=it["status"],
            win=(w1 - w0).total_seconds(),
            cn_on=rel(p["applied_at"]), ran_on=rel(a["applied_at"]),
            cn_off=rel(p["reverted_at"]), ran_off=rel(a["reverted_at"]),
        ))
    return out


def probe_lifecycles():
    seen, out = set(), []
    for d in sorted(os.listdir(LAB)):
        pd = os.path.join(LAB, d, "probe")
        if not os.path.isdir(pd):
            continue
        for f in sorted(os.listdir(pd)):
            if f in seen:
                continue
            seen.add(f)
            first = json.loads(open(os.path.join(pd, f), encoding="utf-8").readline())
            out.append(first)
    return sorted(out, key=lambda r: r["intent_id"])


def numbers():
    print("== Lifecycle (probe first lines), all sessions")
    for r in probe_lifecycles():
        print(f"  intent {r['intent_id']:2d} target={r['target_thp_mbps']:g} "
              f"activated={r['activated_status']:8} final={r['final_status']:8} "
              f"cn={r['cn_activation_delay_s']} ran={r['ran_activation_delay_s']} "
              f"rev={r['reversion_delay_s']}")
    print("== Lifecycle 10/11 from the database (s relative to window start)")
    for r in lifecycle_day2():
        print("  ", {k: (round(v, 1) if isinstance(v, float) else v) for k, v in r.items()})
    if os.path.isdir(LAB3):
        print("== Lifecycle 18/11 from the database (s relative to window start)")
        for r in lifecycle_day3():
            print("  ", {k: (round(v, 1) if isinstance(v, float) else v) for k, v in r.items()})
        print("== 18/11 gNB consoles (after the reversion restart only, no timestamps)")
        for run in ("20261118-1618_e2-rep1", "20261118-1626_e2-rep2", "20261118-1634_e2-rep3"):
            tr = active_series(gnb_trace(os.path.join(LAB3, run, "gnb", "gnb.stdout")), min_mbps=0.0)
            for r, sr in tr.items():
                act = [v for v in sr if v > 50]
                print(f"   {run} rnti={r} secs={len(sr)} active={len(act)}",
                      {k: round(v) for k, v in (describe(act) or {}).items()})

    print("== Radio throughput at the gNB (Mbps per second, active seconds only)")
    one = active_series(gnb_trace(os.path.join(LAB, "20261109-1928_dia-completo", "gnb", "gnb.stdout")))
    for r, s in one.items():
        print("  one UE", r, {k: round(v) for k, v in describe(s).items()})
    two = active_series(gnb_trace(os.path.join(LAB, "20261109-1836_e0", "gnb", "gnb.stdout")))
    both = [i for i in range(len(next(iter(two.values()))))
            if all(s[i] > 50 for s in two.values())]
    print("  two UEs, seconds with both active:", len(both))
    for r, s in two.items():
        print("   ", r, {k: round(v) for k, v in describe([s[i] for i in both]).items()})
    tot = [sum(s[i] for s in two.values()) for i in both]
    print("    total", {k: round(v) for k, v in describe(tot).items()})
    share = [two[list(two)[0]][i] / t for i, t in zip(both, tot)]
    print("    share of first UE: mean %.2f" % st.mean(share))


def fig_cell(out):
    one = active_series(gnb_trace(os.path.join(LAB, "20261109-1928_dia-completo", "gnb", "gnb.stdout")))
    two = active_series(gnb_trace(os.path.join(LAB, "20261109-1836_e0", "gnb", "gnb.stdout")))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(FULL_W, 2.3), sharey=True,
                                 gridspec_kw={"width_ratios": [3, 1.4]})
    (r1, s1), = one.items()
    last = max(i for i, v in enumerate(s1) if v > 50) + 5
    s1 = s1[:last]
    a1.plot(range(len(s1)), s1, color=BLUE, lw=0.7)
    a1.set_title("(a) One UE")
    a1.set_xlabel("Time since gNB start (s)")
    a1.set_ylabel("DL throughput at the gNB (Mbps)")
    # two UEs: only the seconds around both being active
    n = len(next(iter(two.values())))
    idx = [i for i in range(n) if any(s[i] > 50 for s in two.values())]
    i0, i1 = max(0, idx[0] - 2), min(n, idx[-1] + 3)
    xs = list(range(i1 - i0))
    styles = [dict(color=BLUE, ls="-"), dict(color=GREY_D, ls="--")]
    for k, (r, s) in enumerate(two.items()):
        a2.plot(xs, s[i0:i1], lw=0.9, label=f"UE {k + 1}", **styles[k])
    tot = [sum(s[i] for s in two.values()) for i in range(i0, i1)]
    a2.plot(xs, tot, color=GREY, lw=0.9, ls=":", label="Total")
    a2.set_title("(b) Two UEs")
    a2.set_xlabel("Time (s)")
    a2.legend(loc="lower center", frameon=False, ncol=3, bbox_to_anchor=(0.5, -0.36),
              handlelength=1.6, columnspacing=0.8)
    a1.set_ylim(0, 950)
    for a in (a1, a2):
        a.grid(axis="x", visible=False)
    fig.savefig(os.path.join(out, "fig-lab-cell.pdf"))
    plt.close(fig)


def lifecycle_day3():
    return lifecycle_day2(os.path.join(LAB3, "20261118-1635_dia-completo", "db"))


def fig_lifecycle(out):
    rs = lifecycle_day2()
    if os.path.isdir(LAB3):
        rs += lifecycle_day3()
    fig, ax = plt.subplots(figsize=(FULL_W, 0.3 * len(rs) + 0.85))
    for k, r in enumerate(rs):
        y = len(rs) - 1 - k
        ax.add_patch(plt.Rectangle((0, y - 0.32), r["win"], 0.64, color=GREY_L, lw=0, zorder=1))
        ax.plot([r["cn_on"], r["cn_off"]], [y + 0.12] * 2, color=BLUE, lw=2.2, zorder=3,
                solid_capstyle="butt", label="Core policy in force" if k == 0 else None)
        ax.plot([r["ran_on"], r["ran_off"]], [y - 0.12] * 2, color=BLUE_L, lw=2.2, zorder=3,
                solid_capstyle="butt", label="PRB reservation in force" if k == 0 else None)
    ax.set_yticks(range(len(rs)))
    sess = lambda r: 2 if r["intent"] <= 12 else 3  # noqa: E731  10/11 = session 2, 18/11 = 3
    ax.set_yticklabels([f"Session {sess(r)}, intent {r['intent']}" for r in reversed(rs)])
    ax.set_xlabel("Time relative to window start (s)")
    ax.set_xlim(-10, 250)
    ax.set_ylim(-0.6, len(rs) - 0.4)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color="#E5E5E5", lw=0.6)
    win = plt.Rectangle((0, 0), 1, 1, color=GREY_L, lw=0)
    h, l = ax.get_legend_handles_labels()
    ax.legend([win] + h, ["Requested window"] + l, loc="lower center", frameon=False, ncol=3,
              bbox_to_anchor=(0.5, -0.5 if len(rs) <= 3 else -0.32))
    fig.savefig(os.path.join(out, "fig-lab-lifecycle.pdf"))
    plt.close(fig)


# ---------------------------------------------------------------------------
# E2 with saturation (LAB-2026-11-13-saturacao.md): timestamped gNB consoles
# + udp_load.py + one enforcement probe per run folder.
# ---------------------------------------------------------------------------
MARK_SECS = (0, 1)   # udp_load.py --mark: that UE pauses at seconds 0-1 of each minute
SETTLE_S = 15        # seconds after a restart left out of the phase means


def timed_trace(paths):
    """{epoch_second: {rnti: Mbps}} from timestamped consoles (several files)."""
    out = {}
    for path in paths:
        for line in open(path, encoding="utf-8", errors="ignore"):
            m = ROW.match(line)
            if not m or not m.group(1):
                continue
            p = line.split()[1:]
            try:
                v = mbps(p[6])
            except (ValueError, IndexError):
                continue
            out.setdefault(int(float(m.group(1))), {})[m.group(2)] = v
    return out


def _ue_files(run):
    """udp_load.py output in run/ue/*/: {ue: slice}, mark UE."""
    slices, mark = {}, None
    for d in sorted(os.listdir(os.path.join(run, "ue"))) if os.path.isdir(os.path.join(run, "ue")) else []:
        base = os.path.join(run, "ue", d)
        for f in os.listdir(base):
            if f.startswith("sent_") and f.endswith(".csv"):
                ue = f[5:-4]
                ips = [r["ip"] for r in read_csv(os.path.join(base, f)) if r["ip"]]
                ip = ips[-1] if ips else ""
                slices[ue] = 1 if ip.startswith("10.45.") else 2 if ip.startswith("10.46.") else None
        ev = os.path.join(base, "events.csv")
        if os.path.exists(ev):
            for r in read_csv(ev):
                if r["event"] == "start":
                    m = re.search(r"mark=(\S+)", r["detail"])
                    mark = m.group(1) if m else None
    return slices, mark


def _window(run):
    """Window and RAN on/off instants (epoch) of the run's enforcement probe."""
    pdir = os.path.join(run, "probe")
    files = sorted(f for f in os.listdir(pdir) if f.startswith("enforcement-probe_"))
    lc = json.loads(open(os.path.join(pdir, files[-1]), encoding="utf-8").readline())
    w0 = datetime.fromisoformat(lc["window_start"]).timestamp()
    w1 = datetime.fromisoformat(lc["window_end"]).timestamp()
    ran_on = w0 + (lc.get("ran_activation_delay_s") or 0)
    ran_off = w1 + (lc.get("reversion_delay_s") or 0)
    alloc = os.path.join(run, "db", "ran_allocations.csv")
    if os.path.exists(alloc):
        for r in read_csv(alloc):
            if str(r["intent_id"]) == str(lc["intent_id"]):
                ran_on = ts(r["applied_at"]).timestamp()
                if r["reverted_at"]:
                    ran_off = ts(r["reverted_at"]).timestamp()
    return lc, w0, w1, ran_on, ran_off


def e2_run(run, figs=None):
    gdir = os.path.join(run, "gnb")
    consoles = [os.path.join(gdir, f) for f in os.listdir(gdir) if f.startswith("gnb.stdout")]
    tr = timed_trace(consoles)
    if not tr:
        print(f"!! {run}: no timestamped gNB console (gnb.stdout*) — cannot analyse")
        return None
    slices, mark = _ue_files(run)
    lc, w0, w1, ran_on, ran_off = _window(run)

    # which RNTI is which UE: the marked UE is silent at seconds 0-1 of each minute
    secs = sorted(tr)
    rntis = sorted({r for s in secs for r, v in tr[s].items() if v >= 50})
    ue_of = {}
    others = [u for u in slices if u != mark]
    for r in rntis:
        act = [s for s in secs if tr[s].get(r, 0) >= 50]
        if len(act) < 20:
            continue
        span = range(act[0], act[-1] + 1)
        on_mark = [tr.get(s, {}).get(r, 0) for s in span if s % 60 in MARK_SECS]
        off_mark = [tr.get(s, {}).get(r, 0) for s in span if s % 60 not in MARK_SECS and s % 60 != 2]
        ratio = (st.mean(on_mark) / st.mean(off_mark)) if on_mark and st.mean(off_mark) > 0 else 1
        ue_of[r] = mark if (mark and ratio < 0.35) else (others[0] if others else "?")
    ues = sorted(set(ue_of.values()))
    series = {u: {s: sum(v for r, v in tr[s].items() if ue_of.get(r) == u) for s in secs} for u in ues}

    def phase(u, a, b):
        vals = [series[u][s] for s in secs if a <= s < b and s % 60 not in MARK_SECS + (2,)]
        vals = [v for v in vals if v > 1]
        return (st.median(vals), len(vals)) if vals else (None, 0)

    t0, t1 = secs[0], secs[-1]
    # 150 s on each side: the collect range may also hold the previous repetition
    phases = {"before": (max(t0, w0 - 150), w0), "reserved": (ran_on + SETTLE_S, w1),
              "after": (ran_off + SETTLE_S, min(t1 + 1, ran_off + SETTLE_S + 150))}
    t0 = max(t0, int(w0) - 200)
    print(f"== {os.path.basename(run)}  intent {lc['intent_id']} target={lc['target_thp_mbps']:g} Mbps "
          f"SST={lc['sst']} activated={lc['activated_status']} final={lc['final_status']}")
    print(f"   RNTI -> UE: {ue_of}  (mark={mark}; slices {slices})")
    for u in ues:
        row = []
        for k, (a, b) in phases.items():
            med, n = phase(u, a, b)
            row.append(f"{k}={med:6.1f} (n={n})" if med else f"{k}=   --")
        print(f"   UE {u} (SST={slices.get(u)}): " + "  ".join(row))
    for k, (a, b) in phases.items():
        tot = [sum(series[u][s] for u in ues) for s in secs if a <= s < b and s % 60 not in MARK_SECS + (2,)]
        tot = [v for v in tot if v > 1]
        if tot:
            print(f"   total {k}: median {st.median(tot):.0f} Mbps")
    # interruptions: >= 2 consecutive seconds without traffic, outside the mark seconds
    for u in ues:
        gaps, run_start = [], None
        for s in range(t0, t1 + 2):
            silent = series[u].get(s, 0) < 1 and not (u == mark and s % 60 in MARK_SECS)
            if silent and run_start is None:
                run_start = s
            elif not silent and run_start is not None:
                if s - run_start >= 2:
                    gaps.append((run_start - w0, s - run_start))
                run_start = None
        print(f"   UE {u} gaps (start rel. to window, seconds): {gaps}")

    if figs and plt is None:
        print("   (matplotlib not installed: no figure; copy the folder and run it at home)")
    if figs and plt is not None:
        fig, ax = plt.subplots(figsize=(FULL_W, 2.4))
        ax.axvspan(0, w1 - w0, color=GREY_L, lw=0, label="Requested window")
        ax.axvline(ran_on - w0, color=GREY_D, ls="--", lw=0.8)
        ax.axvline(ran_off - w0, color=GREY_D, ls="--", lw=0.8)
        styles = {1: dict(color=BLUE, ls="-"), 2: dict(color=GREY_D, ls="-")}
        for u in ues:
            xs = [s - w0 for s in secs if not (s % 60 in MARK_SECS + (2,))]
            ys = [series[u][s] for s in secs if not (s % 60 in MARK_SECS + (2,))]
            sl = slices.get(u)
            ax.plot(xs, ys, lw=0.8, label=f"UE {u} (SST={sl})", **styles.get(sl, dict(color=GREY)))
        ax.axhline(lc["target_thp_mbps"], color=BLUE, ls=":", lw=0.8, label="Target")
        ax.set_xlabel("Time relative to window start (s)")
        ax.set_ylabel("DL throughput at the gNB (Mbps)")
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.5), ncol=4, frameon=False)
        name = f"fig-lab-e2-{os.path.basename(run)}.pdf"
        fig.savefig(os.path.join(figs, name))
        plt.close(fig)
        print("   figure:", name)
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--figs", help="output folder for the figures")
    ap.add_argument("--e2", nargs="+", metavar="RUN_DIR",
                    help="analyse E2-with-saturation runs (lab-data/<run>) instead of the 09-10/11 data")
    a = ap.parse_args()
    if a.figs:
        os.makedirs(a.figs, exist_ok=True)
    if a.e2:
        for run in a.e2:
            e2_run(run, a.figs)
    else:
        numbers()
        if a.figs:
            fig_cell(a.figs)
            fig_lifecycle(a.figs)
            print("figures written to", a.figs)
