"""
MINAS KPI Collector

Time-series sampler for the MINAS database. Every COLLECT_INTERVAL seconds it
writes one row per slice into:
  - core_kpis  (CN-NSSMF domain: UEs, PDU sessions, aggregate throughput)
  - ran_kpis   (RAN-NSSMF domain: RSRP/SINR/MCS/PRB, per-UE/slice throughput)

It exists so the agents' read tools (get_core_kpis, get_sla_status, ...) and the
NWDAF's predictive model have a history to work on.

Sources are pluggable per table:
  CORE_SOURCE = prometheus | synthetic | mock        (default: prometheus)
  RAN_SOURCE  = prometheus | o1 | synthetic | mock   (default: mock)

  prometheus — query the Prometheus HTTP API over the Open5GS exporters.
  o1         — NETCONF/YANG against the gNB (ideal for RAN; see o1_client.py, not wired).
  synthetic  — per-slice series WITH temporal structure (diurnal + AR(1) +
               bursts, see synth.py); core and RAN share one value per tick.
               Use this, not mock, when anything downstream learns from it
               (NWDAF, RQ4, UC1 tests).
  mock       — i.i.d. uniform rows, only to exercise the pipeline.

Environment
-----------
  PROMETHEUS_URL    default http://prometheus:9090
  COLLECT_INTERVAL  seconds between samples, default 10
  SLICES            comma-separated SST list, default "1,2"
  CORE_SOURCE       default prometheus  (set "mock" for the mocked dev stack —
                    without Prometheus every core sample fails and core_kpis
                    silently stops growing)
  RAN_SOURCE        default mock
  SLICE_TUN         SST -> UPF TUN device, default "1:ogstun,2:ogstun2"
                    (open5gs/upf.yaml: DNN internet -> ogstun, slice2 -> ogstun2)
"""

import json
import os
import random
import sys
import time
from datetime import datetime, timezone

# make agents/ importable (shared db.py) whether run via Docker or `cd agents/collector`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from dotenv import load_dotenv

from db import get_db_conn
from o1_client import get_ran_pm
import synth

load_dotenv()

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
INTERVAL       = int(os.getenv("COLLECT_INTERVAL", "10"))
SLICES         = [int(s) for s in os.getenv("SLICES", "1,2").split(",") if s.strip()]
CORE_SOURCE    = os.getenv("CORE_SOURCE", "prometheus")
RAN_SOURCE     = os.getenv("RAN_SOURCE", "mock")
SLICE_TUN      = dict(
    (int(k), v) for k, v in
    (pair.split(":") for pair in os.getenv("SLICE_TUN", "1:ogstun,2:ogstun2").split(",") if pair.strip())
)

# --- PromQL ---------------------------------------------------------------------
# Throughput is PER SLICE: each slice has its own DNN and UPF TUN device, and
# the upf-netdev exporter (node-exporter in the UPF's network namespace, see
# docker-compose.yml) exposes those interface counters. On a TUN device the
# kernel TRANSMITS what the UPF will encapsulate towards the gNB (downlink)
# and RECEIVES what the UPF decapsulated from the gNB (uplink).
# The old queries used the UPF's N3 packet counters x 1500 bytes: one number
# for all slices (copied into every SST) and an MTU guess instead of bytes.
QUERY_THP_DL = '8 * rate(node_network_transmit_bytes_total{{device="{dev}"}}[1m]) / 1e6'
QUERY_THP_UL = '8 * rate(node_network_receive_bytes_total{{device="{dev}"}}[1m]) / 1e6'
# Open5GS NF-level counters (not per slice).
# TODO(lab): confirm names/labels with  curl <amf|smf>:9090/metrics  — the SMF
# may carry an snssai label on fivegs_smffunction_sm_sessionnbr, which would
# make pdu_sessions per slice too. (gtp2_sessions_active, used before, is the
# 4G SGW-C/PGW-C counter and stays 0 in a 5G SA core.)
QUERY_UES      = "sum(ran_ue)"
QUERY_SESSIONS = "sum(fivegs_smffunction_sm_sessionnbr)"


def _thp_queries(sst: int) -> dict:
    dev = SLICE_TUN.get(sst)
    if dev is None:
        raise ValueError(f"no UPF TUN device mapped to sst={sst} (SLICE_TUN)")
    return {"thp_dl_mbps": QUERY_THP_DL.format(dev=dev),
            "thp_ul_mbps": QUERY_THP_UL.format(dev=dev)}


# --- Prometheus ---------------------------------------------------------------
def prom_scalar(expr: str):
    r = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": expr}, timeout=10)
    r.raise_for_status()
    data = r.json()
    result = data.get("data", {}).get("result", []) if data.get("status") == "success" else []
    if not result:
        return None
    try:
        return round(float(result[0]["value"][1]), 3)
    except (KeyError, IndexError, ValueError):
        return None


# --- samplers: return one dict shaped like the target table's columns --------
_synth_loads: dict[int, synth.SliceLoad] = {}
_synth_rng = random.Random(int(os.getenv("SYNTH_SEED", "0")))
_synth_now: dict[int, float] = {}   # this tick's synthetic thp per slice


def _synthetic_thp(sst: int) -> float:
    if sst not in _synth_now:
        load = _synth_loads.setdefault(sst, synth.SliceLoad(sst, int(os.getenv("SYNTH_SEED", "0"))))
        _synth_now[sst] = load.sample(datetime.now(timezone.utc))
    return _synth_now[sst]


def core_sample(sst: int) -> dict:
    if CORE_SOURCE == "synthetic":
        return synth.core_row(sst, _synthetic_thp(sst), _synth_rng)
    if CORE_SOURCE == "mock":
        return {
            "ues_registered": random.randint(1, 4),
            "pdu_sessions":   random.randint(1, 4),
            "thp_dl_mbps":    round((20.0 if sst == 1 else 5.0) * random.uniform(0.5, 1.2), 2),
            "thp_ul_mbps":    round((20.0 if sst == 1 else 5.0) * 0.3 * random.uniform(0.5, 1.2), 2),
        }
    return {
        "ues_registered": prom_scalar(QUERY_UES),
        "pdu_sessions":   prom_scalar(QUERY_SESSIONS),
        **{kpi: prom_scalar(expr) for kpi, expr in _thp_queries(sst).items()},
    }


def ran_sample(sst: int) -> dict:
    if RAN_SOURCE == "synthetic":
        return synth.ran_row(sst, _synthetic_thp(sst), _synth_rng)
    if RAN_SOURCE == "o1":
        return get_ran_pm(sst)  # raises NotImplementedError until wired
    if RAN_SOURCE == "prometheus":
        # user-plane throughput per slice as seen at the UPF; radio KPIs
        # (RSRP/SINR/MCS/PRB) need the gNB's own metrics
        row = {kpi: prom_scalar(expr) for kpi, expr in _thp_queries(sst).items()}
        row["ue_id"] = f"aggregate-sst{sst}"
        return row
    # mock
    return {
        "ue_id":       f"mock-ue-sst{sst}",
        "rsrp_dbm":    round(random.uniform(-105, -75), 1),
        "sinr_db":     round(random.uniform(5, 25), 1),
        "mcs_dl":      random.randint(6, 27),
        "mcs_ul":      random.randint(4, 20),
        # per slice; two slices together must stay within the 51-PRB cell
        # (RAN_PRB_TOTAL) — the old 5-50 range let them sum to ~100 PRBs
        "prb_used_dl": random.randint(3, 25),
        "prb_used_ul": random.randint(2, 12),
        "thp_dl_mbps": round((20.0 if sst == 1 else 5.0) * random.uniform(0.5, 1.2), 2),
        "thp_ul_mbps": round((20.0 if sst == 1 else 5.0) * 0.3 * random.uniform(0.5, 1.2), 2),
    }


# --- writers ----------------------------------------------------------------
def insert_core(sst: int, k: dict) -> None:
    conn = get_db_conn()
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core_kpis (sst, ues_registered, pdu_sessions, thp_dl_mbps, thp_ul_mbps)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (sst, k.get("ues_registered"), k.get("pdu_sessions"),
             k.get("thp_dl_mbps"), k.get("thp_ul_mbps")),
        )


def insert_ran(sst: int, k: dict) -> None:
    conn = get_db_conn()
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ran_kpis
                (ue_id, sst, rsrp_dbm, sinr_db, mcs_dl, mcs_ul,
                 prb_used_dl, prb_used_ul, thp_dl_mbps, thp_ul_mbps)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (k.get("ue_id", f"aggregate-sst{sst}"), sst,
             k.get("rsrp_dbm"), k.get("sinr_db"), k.get("mcs_dl"), k.get("mcs_ul"),
             k.get("prb_used_dl"), k.get("prb_used_ul"),
             k.get("thp_dl_mbps"), k.get("thp_ul_mbps")),
        )


def tick() -> None:
    _synth_now.clear()
    for sst in SLICES:
        try:
            core = core_sample(sst)
            insert_core(sst, core)
            print(f"[collector] core sst={sst} {json.dumps(core)}")
        except Exception as exc:
            print(f"[collector] core sst={sst} error: {exc}")
        try:
            ran = ran_sample(sst)
            insert_ran(sst, ran)
            print(f"[collector] ran  sst={sst} {json.dumps(ran)}")
        except NotImplementedError as exc:
            print(f"[collector] ran  sst={sst} skipped: {exc}")
        except Exception as exc:
            print(f"[collector] ran  sst={sst} error: {exc}")


if __name__ == "__main__":
    print(f"[collector] core={CORE_SOURCE} ran={RAN_SOURCE} "
          f"prometheus={PROMETHEUS_URL} slices={SLICES} interval={INTERVAL}s")
    while True:
        tick()
        time.sleep(INTERVAL)
