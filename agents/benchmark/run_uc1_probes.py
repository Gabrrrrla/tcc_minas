"""
Probes of the proactive loop of Use Case 1 (cn-nssmf/monitor.py ->
orchestrator /event), which the benchmark does not exercise: there, a UC1
intent is only submitted and sized once.

Each probe:
  1. takes over the telemetry: the collector is stopped and this script writes
     core_kpis/ran_kpis every 10 s for both slices, with the same synthetic
     model as the collector (synth.py) but without random bursts, so the only
     demand change is the injected one;
  2. submits a real windowless UC1 intent through the orchestrator,
     "keep G Mbps guaranteed on the URLLC slice (SST=2)", and waits for it to be
     applied;
  3. after a baseline, injects extra demand on SST=2: a RAMP (+A Mbps over
     ramp_s) or a STEP (+A Mbps at once), held for hold_s;
  4. records, from the database: the first sample above G (demand exceeds the
     guarantee) and above G*(1+margin) (the monitor's threshold), the UC1 event
     (time and predicted demand), and the new policy and RAN allocation of the
     intent (time, new guarantee, status).

Reported per probe (seconds, relative to the first sample above G):
  event_s     when the monitor reported the predicted exhaustion
  core_s/ran_s when the higher guarantee was in force in each domain
Negative = before the demand exceeded the guarantee (proactive).

Nothing in the system is changed; the monitor runs with its defaults
(poll 30 s, margin 10 %, horizon 60 s, cooldown 300 s).

    POSTGRES_HOST=localhost python agents/benchmark/run_uc1_probes.py --reps 3
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
AGENTS = os.path.dirname(HERE)
REPO = os.path.dirname(AGENTS)
sys.path.insert(0, AGENTS)
sys.path.insert(0, os.path.join(AGENTS, "collector"))
sys.path.insert(0, HERE)

import synth  # noqa: E402
from db import get_db_conn  # noqa: E402
from run_benchmark import release  # noqa: E402

for p in synth.PROFILES.values():   # only the injected change, no random bursts
    p["burst_per_hour"] = 0.0

MARGIN = 0.10


class Writer(threading.Thread):
    """Writes one synthetic sample per slice every `interval` s, plus the
    injected extra demand on SST=2 (set through .extra)."""

    def __init__(self, interval: float = 10.0, seed: int = 7):
        super().__init__(daemon=True)
        self.interval = interval
        self.loads = {s: synth.SliceLoad(s, seed) for s in (1, 2)}
        self.rng = random.Random(seed)
        self.extra = 0.0
        self.samples: list[tuple[datetime, float]] = []   # SST=2 demand written
        self.stop_ev = threading.Event()

    def run(self) -> None:
        conn = get_db_conn()
        while not self.stop_ev.is_set():
            t = datetime.now(timezone.utc)
            for sst in (1, 2):
                thp = self.loads[sst].sample(t)
                if sst == 2:
                    thp = round(thp + self.extra, 3)
                    self.samples.append((t, thp))
                core = synth.core_row(sst, thp, self.rng)
                ran = synth.ran_row(sst, thp, self.rng)
                with conn, conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO core_kpis (sst, ues_registered, pdu_sessions, thp_dl_mbps, thp_ul_mbps) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (sst, core["ues_registered"], core["pdu_sessions"], core["thp_dl_mbps"], core["thp_ul_mbps"]))
                    cur.execute(
                        "INSERT INTO ran_kpis (ue_id, sst, rsrp_dbm, sinr_db, mcs_dl, mcs_ul, prb_used_dl, "
                        "prb_used_ul, thp_dl_mbps, thp_ul_mbps) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (ran["ue_id"], sst, ran["rsrp_dbm"], ran["sinr_db"], ran["mcs_dl"], ran["mcs_ul"],
                         ran["prb_used_dl"], ran["prb_used_ul"], ran["thp_dl_mbps"], ran["thp_ul_mbps"]))
            self.stop_ev.wait(self.interval)


def docker(*args: str) -> None:
    subprocess.run(["docker", *args], cwd=REPO, capture_output=True, check=True)


def intent_rows(conn, iid: int) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM intents WHERE id = %s", (iid,))
        status = cur.fetchone()[0]
        cur.execute("SELECT applied_at, gbr_dl_mbps, status FROM policies WHERE intent_id = %s ORDER BY applied_at",
                    (iid,))
        pol = cur.fetchall()
        cur.execute("SELECT applied_at, supported_thp_mbps, prb_allocated, status FROM ran_allocations "
                    "WHERE intent_id = %s ORDER BY applied_at", (iid,))
        ran = cur.fetchall()
        cur.execute("SELECT created_at, type, payload, outcome FROM events WHERE intent_id = %s ORDER BY created_at",
                    (iid,))
        ev = cur.fetchall()
    return {"status": status, "policies": pol, "ran": ran, "events": ev}


def run_probe(conn, writer: Writer, kind: str, rep: int, args) -> dict:
    g = args.guarantee
    text = f"Keep {g:g} Mbps guaranteed on the URLLC slice (SST=2)."
    print(f"\n[{kind} #{rep}] {text}", flush=True)
    writer.extra = 0.0
    body = requests.post(f"{args.host}/intent", json={"intent": text}, timeout=240).json()
    ids = body.get("intent_ids") or []
    if not ids:
        return {"kind": kind, "rep": rep, "error": "intent not recorded", "outcome": str(body.get("outcome"))[:200]}
    iid = ids[0]
    st0 = intent_rows(conn, iid)
    print(f"[{kind} #{rep}] intent {iid}: {st0['status']}", flush=True)
    if st0["status"] not in ("applied", "degraded"):
        release(conn, [iid])
        return {"kind": kind, "rep": rep, "intent_id": iid, "error": f"not in force: {st0['status']}"}

    time.sleep(args.baseline_s)
    t_inj = datetime.now(timezone.utc)
    print(f"[{kind} #{rep}] injecting +{args.amount} Mbps ({kind})", flush=True)
    t_end = time.monotonic() + args.ramp_s + args.hold_s
    while time.monotonic() < t_end:
        el = (datetime.now(timezone.utc) - t_inj).total_seconds()
        writer.extra = args.amount if kind == "step" else args.amount * min(1.0, el / args.ramp_s)
        time.sleep(2)
    writer.extra = 0.0
    time.sleep(5)

    st = intent_rows(conn, iid)
    samples = [(t, v) for t, v in writer.samples if t >= t_inj]
    t_g = next((t for t, v in samples if v > g), None)
    t_thr = next((t for t, v in samples if v > g * (1 + MARGIN)), None)
    rel = lambda t: None if t is None or t_g is None else round((t - t_g).total_seconds(), 1)  # noqa: E731
    ev = [e for e in st["events"] if e[1] == "predicted_exhaustion" and e[0] >= t_inj]
    pol = [p for p in st["policies"] if p[0] and p[0] >= t_inj]
    ran = [r for r in st["ran"] if r[0] and r[0] >= t_inj]
    demand_at = lambda t: next((v for ts, v in samples if ts >= t), None)  # noqa: E731
    res = {
        "kind": kind, "rep": rep, "intent_id": iid, "guarantee_mbps": g, "amount_mbps": args.amount,
        "ramp_s": args.ramp_s if kind == "ramp" else 0, "injection_at": t_inj.isoformat(),
        "demand_exceeds_g_at": t_g.isoformat() if t_g else None,
        "threshold_s": rel(t_thr),
        "n_events": len(ev),
        "event_s": rel(ev[0][0]) if ev else None,
        "event_predicted_mbps": ev[0][2].get("predicted_thp_mbps") if ev else None,
        "demand_at_event_mbps": demand_at(ev[0][0]) if ev else None,
        "demand_60s_after_event_mbps": demand_at(ev[0][0] + timedelta(seconds=60)) if ev else None,
        "event_outcome": ev[0][3] if ev else None,
        "core_s": rel(pol[0][0]) if pol else None,
        "new_gbr_mbps": float(pol[-1][1]) if pol else None,
        "ran_s": rel(ran[0][0]) if ran else None,
        "ran_supported_mbps": float(ran[-1][1]) if ran and ran[-1][1] is not None else None,
        "final_status": st["status"],
        "peak_demand_mbps": max((v for _, v in samples), default=None),
    }
    print(f"[{kind} #{rep}] {json.dumps(res)}", flush=True)
    release(conn, [iid])
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="http://localhost:8000")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--kinds", default="ramp,step")
    ap.add_argument("--guarantee", type=float, default=6.0)
    ap.add_argument("--amount", type=float, default=8.0)
    ap.add_argument("--ramp-s", type=float, default=180.0)
    ap.add_argument("--hold-s", type=float, default=240.0)
    ap.add_argument("--baseline-s", type=float, default=60.0)
    ap.add_argument("--warmup-s", type=float, default=120.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    out = args.out or os.path.join(HERE, "results", "uc1-probes",
                                   f"uc1-probes_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    conn = get_db_conn()
    docker("stop", "collector")
    writer = Writer()
    writer.start()
    try:
        time.sleep(args.warmup_s)
        for rep in range(1, args.reps + 1):
            for kind in args.kinds.split(","):
                try:
                    r = run_probe(conn, writer, kind, rep, args)
                except Exception as exc:  # noqa: BLE001
                    r = {"kind": kind, "rep": rep, "error": repr(exc)}
                with open(out, "a", encoding="utf-8") as f:
                    f.write(json.dumps(r, default=str) + "\n")
                time.sleep(args.baseline_s)   # let the series settle before the next probe
    finally:
        writer.stop_ev.set()
        docker("start", "collector")
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
