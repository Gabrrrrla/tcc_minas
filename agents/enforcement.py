"""
agents/enforcement.py
---------------------
Client side of real enforcement, shared by the two domain agents.

ENFORCEMENT_MODE=mock (default): nothing leaves the agent; the tools only
    persist the decision (policies / ran_allocations), as in every benchmark
    round. Keeps the benchmark comparable and runnable without a testbed.
ENFORCEMENT_MODE=real: the decision is pushed to the testbed BEFORE it is
    persisted, and a push failure is returned to the agent as a failed tool
    call, so nothing is recorded as in force unless the equipment accepted it.

    Core  -> upf-shaper (agents/enforcers/upf_shaper.py), sidecar in the UPF's
             network namespace: per-slice DL rate limit (MBR / Session-AMBR,
             enforced at the UPF as in TS 23.501 5.7.1.8) with tc on the
             slice's TUN device (ogstun / ogstun2).
    RAN   -> gnb-slicer (agents/enforcers/gnb_slicer.py), on the gNB host:
             per-slice min/max PRB policy ratio in the srsRAN gNB config,
             gNB restarted only when the ratios change.

Both endpoints take the FULL desired state of a slice (core) or of the cell
(RAN), never a delta, so a retry or a lost response cannot drift the testbed.
"""

from __future__ import annotations

import os
import time

import requests

MODE = os.getenv("ENFORCEMENT_MODE", "mock").strip().lower()
UPF_SHAPER_URL = os.getenv("UPF_SHAPER_URL", "http://upf:9300")
GNB_SLICER_URL = os.getenv("GNB_SLICER_URL", "http://10.11.0.1:8010")
# a gNB restart takes several seconds; the slicer answers after it is back
TIMEOUT_S = float(os.getenv("ENFORCEMENT_TIMEOUT_S", "90"))
# UPF_SHAPING=false keeps the core push but never limits the slice (the UPF is
# told "no limit"). Needed when the experiment must SEE demand above the
# guarantee: with the MBR at the guarantee, the throughput the collector reads
# at the UPF can never exceed it, so the UC1 loop could not detect a rise.
UPF_SHAPING = os.getenv("UPF_SHAPING", "true").strip().lower() not in ("false", "0", "no")


class EnforcementError(RuntimeError):
    pass


def is_real() -> bool:
    return MODE == "real"


def _put(url: str, body: dict) -> dict:
    t0 = time.monotonic()
    try:
        r = requests.put(url, json=body, timeout=TIMEOUT_S)
    except requests.RequestException as e:
        raise EnforcementError(f"{url}: {e}") from e
    if r.status_code >= 300:
        raise EnforcementError(f"{url}: HTTP {r.status_code} {r.text[:300]}")
    out = r.json()
    out["enforcement_latency_s"] = round(time.monotonic() - t0, 3)
    return out


def push_core(sst: int, mbr_dl_mbps: float | None) -> dict:
    """Set (or clear, with None) the DL rate limit of the slice at the UPF."""
    if not is_real():
        return {"mode": "mock"}
    if not UPF_SHAPING:
        mbr_dl_mbps = None
    res = _put(f"{UPF_SHAPER_URL}/slices/{sst}", {"mbr_dl_mbps": mbr_dl_mbps})
    print(f"[enforcement] core sst={sst} mbr_dl={mbr_dl_mbps} -> {res}")
    return {"mode": "real", "target": "upf", **res}


def push_ran(prb_total: int, reservations: dict[int, int | None]) -> dict:
    """Set the PRB reservation of every slice of the cell (None = none)."""
    if not is_real():
        return {"mode": "mock"}
    res = _put(f"{GNB_SLICER_URL}/slicing",
               {"prb_total": prb_total, "slices": {str(k): v for k, v in reservations.items()}})
    print(f"[enforcement] ran reservations={reservations} -> {res}")
    return {"mode": "real", "target": "srsran-gnb", **res}
