"""
agents/enforcers/upf_shaper.py
------------------------------
Core-side enforcement point. Runs as a sidecar sharing the UPF's network
namespace (docker-compose.lab.yml: network_mode "service:upf", NET_ADMIN),
so it sees the per-slice TUN devices the UPF writes downlink packets to:

    SST=1 (DNN internet) -> ogstun     SST=2 (DNN slice2) -> ogstun2

Downlink traffic for a UE leaves the kernel through that device's egress
queue, so a token-bucket qdisc there caps the slice's aggregate DL rate:
the UPF-side MBR / Session-AMBR enforcement of TS 23.501 5.7.1.8, applied per
slice instead of per session.

API (port SHAPER_PORT, default 9300):
    PUT /slices/<sst>  {"mbr_dl_mbps": 12.5}   -> tbf at 12.5 Mbit/s
    PUT /slices/<sst>  {"mbr_dl_mbps": null}   -> qdisc removed (no limit)
    GET /slices                                -> qdisc + counters per slice
    GET /health
"""

from __future__ import annotations

import os
import re
import subprocess

from flask import Flask, jsonify, request

SLICE_TUN = dict(
    (int(k), v) for k, v in
    (p.split(":") for p in os.getenv("SLICE_TUN", "1:ogstun,2:ogstun2").split(",") if p.strip())
)
MAX_MBPS = float(os.getenv("SHAPER_MAX_MBPS", "1000"))
LATENCY_MS = int(os.getenv("SHAPER_LATENCY_MS", "50"))

app = Flask(__name__)


def _tc(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["tc", *args]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}: {p.stderr.strip()}")
    return p


def _show(dev: str) -> dict:
    out = _tc("-s", "qdisc", "show", "dev", dev, "root", check=False).stdout
    rate = re.search(r"tbf .*?rate (\S+)", out)
    sent = re.search(r"Sent (\d+) bytes (\d+) pkt \(dropped (\d+)", out)
    return {
        "dev": dev,
        "limited": bool(rate),
        "rate": rate.group(1) if rate else None,
        "sent_bytes": int(sent.group(1)) if sent else None,
        "dropped_pkts": int(sent.group(3)) if sent else None,
        "raw": out.strip(),
    }


@app.get("/health")
def health():
    return jsonify({"status": "ok", "agent": "upf-shaper", "slices": SLICE_TUN})


@app.get("/slices")
def slices():
    return jsonify({str(s): _show(d) for s, d in SLICE_TUN.items()})


@app.put("/slices/<int:sst>")
def set_slice(sst: int):
    dev = SLICE_TUN.get(sst)
    if dev is None:
        return jsonify({"error": f"unknown sst {sst}", "known": list(SLICE_TUN)}), 404
    body = request.get_json(force=True, silent=True) or {}
    mbps = body.get("mbr_dl_mbps")
    try:
        if mbps is None:
            # no limit in force for the slice: back to the device default
            _tc("qdisc", "del", "dev", dev, "root", check=False)
        else:
            mbps = float(mbps)
            if not 0 < mbps <= MAX_MBPS:
                return jsonify({"error": f"mbr_dl_mbps must be in (0, {MAX_MBPS}]"}), 400
            # bucket of ~10 ms at the target rate, never below 2 MTU-sized packets
            burst = max(3000, int(mbps * 1e6 / 8 * 0.010))
            _tc("qdisc", "replace", "dev", dev, "root", "tbf",
                "rate", f"{mbps}mbit", "burst", str(burst), "latency", f"{LATENCY_MS}ms")
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    state = _show(dev)
    print(f"[upf-shaper] sst={sst} dev={dev} mbr_dl={mbps} -> rate={state['rate']}", flush=True)
    return jsonify({"sst": sst, "mbr_dl_mbps": mbps, **state})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("SHAPER_PORT", "9300")))
