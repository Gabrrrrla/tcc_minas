"""
MINAS KPI Collector

Time-series sampler for the MINAS database. Every COLLECT_INTERVAL seconds it
writes one row per slice into:
  - core_kpis  (CN-NSSMF domain: UEs, PDU sessions, aggregate throughput)
  - ran_kpis   (RAN-NSSMF domain: RSRP/SINR/MCS/PRB, per-UE/slice throughput)

It exists so the agents' read tools (get_core_kpis, get_sla_status, ...) and the
NWDAF's predictive model have a history to work on.

Sources are pluggable per table:
  CORE_SOURCE = prometheus | mock          (default: prometheus)
  RAN_SOURCE  = prometheus | o1 | mock     (default: mock)

  prometheus — query the Prometheus HTTP API over the Open5GS exporters.
  o1         — NETCONF/YANG against the gNB (ideal for RAN; see o1_client.py, not wired).
  mock       — synthetic rows, to develop the pipeline before real metrics exist.

Environment
-----------
  PROMETHEUS_URL    default http://prometheus:9090
  COLLECT_INTERVAL  seconds between samples, default 10
  SLICES            comma-separated SST list, default "1,2"
  CORE_SOURCE       default prometheus
  RAN_SOURCE        default mock
"""

import json
import os
import random
import sys
import time

# make agents/ importable (shared db.py) whether run via Docker or `cd agents/collector`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from dotenv import load_dotenv

from db import get_db_conn
from o1_client import get_ran_pm

load_dotenv()

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
INTERVAL       = int(os.getenv("COLLECT_INTERVAL", "10"))
SLICES         = [int(s) for s in os.getenv("SLICES", "1,2").split(",") if s.strip()]
CORE_SOURCE    = os.getenv("CORE_SOURCE", "prometheus")
RAN_SOURCE     = os.getenv("RAN_SOURCE", "mock")

# --- PromQL ---------------------------------------------------------------------
# Open5GS 2.6.4 exposes very few slice-labelled metrics, so the aggregate value is
# attributed to every configured slice for now.
# TODO: add a per-slice matcher once the NF metrics carry an snssai/dnn label.
#       Confirm names with:  curl smf:9090/metrics | grep -E 'snssai|session'
QUERIES_CORE = {
    "ues_registered": "sum(ran_ue)",
    "pdu_sessions":   "sum(gtp2_sessions_active)",
    "thp_dl_mbps":    "8 * 1500 * sum(rate(fivegs_ep_n3_gtp_outdatapktn3upf[1m])) / 1e6",
    "thp_ul_mbps":    "8 * 1500 * sum(rate(fivegs_ep_n3_gtp_indatapktn3upf[1m]))  / 1e6",
}
# Until srsRAN exposes an exporter, only throughput can be approximated (from the
# UPF N3 GTP counters — HANDOVER-2026-08-25 "Opção 1"). Radio-layer KPIs
# (RSRP/SINR/MCS/PRB) require the O1 interface — RAN_SOURCE=o1, see o1_client.py.
QUERIES_RAN = {
    "thp_dl_mbps": "8 * 1500 * sum(rate(fivegs_ep_n3_gtp_outdatapktn3upf[1m])) / 1e6",
    "thp_ul_mbps": "8 * 1500 * sum(rate(fivegs_ep_n3_gtp_indatapktn3upf[1m]))  / 1e6",
}


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
def core_sample(sst: int) -> dict:
    if CORE_SOURCE == "mock":
        return {
            "ues_registered": random.randint(1, 4),
            "pdu_sessions":   random.randint(1, 4),
            "thp_dl_mbps":    round((20.0 if sst == 1 else 5.0) * random.uniform(0.5, 1.2), 2),
            "thp_ul_mbps":    round((20.0 if sst == 1 else 5.0) * 0.3 * random.uniform(0.5, 1.2), 2),
        }
    return {kpi: prom_scalar(expr) for kpi, expr in QUERIES_CORE.items()}


def ran_sample(sst: int) -> dict:
    if RAN_SOURCE == "o1":
        return get_ran_pm(sst)  # raises NotImplementedError until wired
    if RAN_SOURCE == "prometheus":
        row = {kpi: prom_scalar(expr) for kpi, expr in QUERIES_RAN.items()}
        row["ue_id"] = f"aggregate-sst{sst}"
        return row
    # mock
    return {
        "ue_id":       f"mock-ue-sst{sst}",
        "rsrp_dbm":    round(random.uniform(-105, -75), 1),
        "sinr_db":     round(random.uniform(5, 25), 1),
        "mcs_dl":      random.randint(6, 27),
        "mcs_ul":      random.randint(4, 20),
        "prb_used_dl": random.randint(5, 50),
        "prb_used_ul": random.randint(2, 25),
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
