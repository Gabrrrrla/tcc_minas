"""
agents/benchmark/run_enforcement_probe.py
-----------------------------------------
Lab-only: shows that a windowed intent CHANGES THE TRAFFIC, not just the
database. Same lifecycle as run_window_probes.py (natural-language intent ->
scheduled -> activated at window_start -> reverted at window_end), but with
ENFORCEMENT_MODE=real and a UE pulling traffic through the slice, while this
script samples the slice's measured DL throughput at the UPF (Prometheus,
upf-netdev on ogstun/ogstun2) from before the window opens until after it
closes. Two designs (LAB-PLAN-2026-11-03.md, decision 1):
  B, one UE (UPF_SHAPING=true): throughput at the UE's offered load, dropping
     to the intent's target inside the window (the core's MBR ceiling), back
     up after the reversion. Shows that both enforcement points act, not a
     guarantee.
  A, two UEs on the two slices, both above the cell's capacity
     (UPF_SHAPING=false): without an intent they share the cell; inside the
     window the PRB reservation should RAISE the target slice to the target
     while the other falls. This is what shows the guarantee.

Before running (docker-compose.lab.yml stack up, gnb_slicer.py running, UE
attached on the eMBB slice), start the demand ON THE UE, above the target:
    iperf3 -c 10.11.0.70 -R -b 15M -t 1200
then, on the host:
    POSTGRES_HOST=localhost python agents/benchmark/run_enforcement_probe.py --target 5

Output: results/enforcement-probe_<ts>.jsonl, first line the lifecycle result
(same fields as the window probes), then one line per throughput sample.
Plot with: python agents/benchmark/plots.py ... --enforcement <that file>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from db import get_db_conn  # noqa: E402
from run_window_probes import _state, evaluate  # noqa: E402
from timeutil import now  # noqa: E402

SLICE_TUN = dict(
    (int(k), v) for k, v in
    (p.split(":") for p in os.getenv("SLICE_TUN", "1:ogstun,2:ogstun2").split(",") if p.strip())
)


def thp_dl(prom: str, dev: str, rate_window: str) -> float | None:
    q = f'8 * rate(node_network_transmit_bytes_total{{device="{dev}"}}[{rate_window}]) / 1e6'
    try:
        res = requests.get(f"{prom}/api/v1/query", params={"query": q}, timeout=5).json()
        vals = res["data"]["result"]
        return round(float(vals[0]["value"][1]), 3) if vals else None
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="http://localhost:8000")
    ap.add_argument("--prom", default="http://localhost:9090")
    ap.add_argument("--target", type=float, default=5.0, help="Mbps; keep it below the UE's offered load")
    ap.add_argument("--sst", type=int, default=1)
    ap.add_argument("--lead-min", type=float, default=3.0)
    ap.add_argument("--duration-min", type=int, default=3)
    ap.add_argument("--margin-s", type=int, default=90, help="sampling before/after the window")
    ap.add_argument("--every-s", type=float, default=2.0)
    ap.add_argument("--rate-window", default="20s", help="PromQL rate() window (scrape is 10 s)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    start = (now() + timedelta(minutes=a.lead_min)).replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = start + timedelta(minutes=a.duration_min)
    slice_name = {1: "eMBB slice (SST=1)", 2: "URLLC slice (SST=2)"}[a.sst]
    p = {"probe": 0, "target_thp_mbps": a.target, "window_start": start, "window_end": end,
         "text": (f"Guarantee {a.target:g} Mbps on the {slice_name} from {start:%H:%M} to "
                  f"{end:%H:%M} today ({start:%Y-%m-%d}).")}

    if thp_dl(a.prom, SLICE_TUN[a.sst], a.rate_window) is None:
        sys.exit(f"no throughput series for {SLICE_TUN[a.sst]} at {a.prom} (upf-netdev up?)")

    print(f"[enf-probe] {p['text']}")
    body = requests.post(f"{a.host}/intent", json={"intent": p["text"]}, timeout=300).json()
    ids = body.get("intent_ids") or []
    if not ids:
        sys.exit(f"no intent recorded: {str(body.get('outcome'))[:200]}")
    conn = get_db_conn()
    p["intent_id"] = ids[0]
    p["recorded_status"] = _state(conn, ids[0])["status"]
    print(f"[enf-probe] intent {ids[0]} '{p['recorded_status']}', window {start:%H:%M}-{end:%H:%M}")

    samples, activated = [], None
    t_from = start - timedelta(seconds=a.margin_s)
    t_to = end + timedelta(seconds=a.margin_s)
    while now() < t_to:
        t = now()
        if t >= t_from:
            st = _state(conn, ids[0])
            if activated is None and st["status"] in ("applied", "degraded", "failed"):
                activated = st["status"]
            s = {"t": t.isoformat(), "rel_s": round((t - start).total_seconds(), 1),
                 "status": st["status"],
                 "thp_dl_mbps": {str(k): thp_dl(a.prom, d, a.rate_window) for k, d in SLICE_TUN.items()}}
            samples.append(s)
            print(f"  {s['rel_s']:>7.1f}s  {s['status']:9}  " +
                  "  ".join(f"sst{k}={v}" for k, v in s["thp_dl_mbps"].items()))
        time.sleep(a.every_s)

    result = evaluate(p, _state(conn, ids[0]), activated)
    out = a.out or os.path.join(HERE, "results",
                                f"enforcement-probe_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "lifecycle", "sst": a.sst, **result}) + "\n")
        for s in samples:
            f.write(json.dumps({"kind": "sample", **s}) + "\n")
    print(f"[enf-probe] {result['final_status']}, activation {result['activation_delay_s']} s, "
          f"reversion {result['reversion_delay_s']} s -> {out}")


if __name__ == "__main__":
    main()
