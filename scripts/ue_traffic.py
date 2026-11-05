#!/usr/bin/env python3
"""MINAS lab — drives DOWNLINK TCP traffic to the UEs and logs it, from the server.

The phones only run an iperf3 SERVER (Termux: `pkg install iperf3; iperf3 -s`,
or an app with server mode). This script runs the iperf3 CLIENT inside the
UPF's network namespace (where 10.45/16 and 10.46/16 live on ogstun/ogstun2),
so the traffic takes the real path UPF -> GTP-U -> gNB -> radio -> UE, and
everything is timestamped with the SERVER clock and saved with no copying
from the phones.

    python3 scripts/ue_traffic.py --find                     # UE IPs from the SMF log
    python3 scripts/ue_traffic.py --ue A=10.45.0.2 --duration 60 --label e0-ueA-rep1
    python3 scripts/ue_traffic.py --ue A=10.45.0.2 --ue B=10.46.0.2 --duration 900 --label e2-rep1
    python3 scripts/ue_traffic.py --ue A=10.45.0.2 --rate 15M --duration 900 --label e1-rep1
    python3 scripts/ue_traffic.py --ue B=10.46.0.2 --phases 4M:180,12M:360,4M:300 --label e4

Output: lab-data/ue-traffic/<YYYYmmdd-HHMMSS>_<label>/
    <ue>.log        raw iperf3 output (client side + the phone's own report)
    <ue>.csv        one row per second: wall clock, phase, sent Mbps (live)
    <ue>_rx.csv     one row per second as RECEIVED by the phone (from
                    --get-server-output; written when each iperf run ends)
    events_<ue>.csv starts, ends, early exits (radio outage / gNB restart)
scripts/lab_collect.sh copies the folders written in its time range into ue/.

After an outage the SENT column shows a catch-up spike (iperf3 -b paces on
the average); the radio caps it, so read delivered throughput from <ue>_rx.csv.

If an iperf run dies early (the gNB restarted and TCP gave up), it is started
again after 2 s for the rest of the phase, and the gap is in events.csv.
Stdlib only. Needs docker and the networkstatic/iperf3 image (used by the
lab compose file).
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = os.getenv("IPERF_IMAGE", "networkstatic/iperf3")
NETNS = os.getenv("IPERF_NETNS", "container:upf")

# "[  5]   3.00-4.00   sec  12.5 MBytes   105 Mbits/sec ..." (also [SUM] with -P)
LINE = re.compile(r"^\[\s*(SUM|\d+)\]\s+(\d+\.\d+)-(\d+\.\d+)\s+sec\s+[\d.]+\s+\w?Bytes\s+([\d.]+)\s+([KMG]?)bits/sec(.*)$")
UNIT = {"": 1e-6, "K": 1e-3, "M": 1.0, "G": 1e3}
# Open5GS 2.6 SMF: "UE SUPI[imsi-001010000000001] DNN[internet] IPv4[10.45.0.2] IPv6[]"
SMF_UE = re.compile(r"SUPI\[(imsi-\d+)\]\s+DNN\[([^\]]*)\]\s+IPv4\[([\d.]+)\]")

print_lock = threading.Lock()


def log(msg: str) -> None:
    with print_lock:
        print(msg, flush=True)


def find_ues() -> None:
    out = subprocess.run(["docker", "logs", "smf"], capture_output=True, text=True)
    last = {}
    for line in (out.stdout + out.stderr).splitlines():
        m = SMF_UE.search(line)
        if m:
            last[(m.group(1), m.group(2))] = m.group(3)
    if not last:
        print("no UE session in `docker logs smf` yet (UEs attached? stack up?)")
        return
    for (supi, dnn), ip in sorted(last.items()):
        sl = "SST 1 (eMBB)" if ip.startswith("10.45.") else "SST 2" if ip.startswith("10.46.") else "?"
        print(f"{supi}  DNN={dnn:9}  IPv4={ip:15}  {sl}")
    print("(latest session per SUPI/DNN; a UE that re-attached shows its newest IP)")


def parse_phases(a) -> list[tuple[str, int]]:
    if a.phases:
        out = []
        for p in a.phases.split(","):
            rate, secs = p.split(":")
            out.append((rate.strip(), int(secs)))
        return out
    return [(a.rate, a.duration)]


def mbps(line: str):
    m = LINE.match(line.strip())
    if not m or m.group(6).strip().endswith(("sender", "receiver")):
        return None
    return m.group(1), float(m.group(2)), float(m.group(3)), float(m.group(4)) * UNIT[m.group(5)]


class Runner(threading.Thread):
    def __init__(self, name, ip, phases, a, outdir, stop):
        super().__init__(daemon=True)
        self.name_, self.ip, self.phases, self.a, self.outdir, self.stop = name, ip, phases, a, outdir, stop
        self.last = {}  # latest sent Mbps, for the live status line
        self.raw = open(os.path.join(outdir, f"{name}.log"), "a", encoding="utf-8")
        self.tx = csv.writer(open(os.path.join(outdir, f"{name}.csv"), "a", newline="", encoding="utf-8"))
        self.rx = csv.writer(open(os.path.join(outdir, f"{name}_rx.csv"), "a", newline="", encoding="utf-8"))
        self.ev = csv.writer(open(os.path.join(outdir, f"events_{name}.csv"), "a", newline="", encoding="utf-8"))
        self.tx.writerow(["t_epoch", "t_iso", "ue", "ip", "phase", "rate", "mbps_sent"])
        self.rx.writerow(["t_epoch", "t_iso", "ue", "ip", "phase", "rate", "mbps_received"])
        self.ev.writerow(["t_epoch", "t_iso", "ue", "event", "detail"])

    def event(self, what, detail=""):
        t = time.time()
        self.ev.writerow([f"{t:.3f}", datetime.fromtimestamp(t).isoformat(timespec="seconds"), self.name_, what, detail])
        log(f"[{datetime.now():%H:%M:%S}] {self.name_}: {what} {detail}")

    def run_once(self, phase, rate, secs) -> int:
        cmd = ["docker", "run", "--rm", "--label", "minas-ue-traffic=1", "--network", NETNS, IMAGE,
               "-c", self.ip, "-p", str(self.a.port), "-t", str(secs), "-i", "1", "-f", "m",
               "-b", rate, "-P", str(self.a.parallel), "--forceflush", "--get-server-output",
               "--connect-timeout", "5000"]
        self.raw.write(f"\n### {datetime.now().isoformat(timespec='seconds')} {' '.join(cmd)}\n")
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        t0, in_server, server_rows = None, False, []
        for line in p.stdout:
            self.raw.write(line)
            if self.stop.is_set():
                p.terminate()
                break
            if line.startswith("Server output:"):
                in_server = True
                continue
            r = mbps(line)
            if not r:
                continue
            sid, start, end, v = r
            if self.a.parallel > 1 and sid != "SUM":
                continue
            if in_server:
                server_rows.append((start, v))
                continue
            now = time.time()
            if t0 is None:
                t0 = now - end  # wall clock of the iperf run's second 0
            t = t0 + end
            self.tx.writerow([f"{t:.3f}", datetime.fromtimestamp(t).isoformat(timespec="seconds"),
                              self.name_, self.ip, phase, rate, f"{v:.2f}"])
            self.last = {"t": now, "v": v}
        p.wait()
        if t0 is not None:
            for start, v in server_rows:
                t = t0 + start + 1
                self.rx.writerow([f"{t:.3f}", datetime.fromtimestamp(t).isoformat(timespec="seconds"),
                                  self.name_, self.ip, phase, rate, f"{v:.2f}"])
        self.raw.flush()
        return p.returncode

    def run(self):
        for i, (rate, secs) in enumerate(self.phases, 1):
            end = time.time() + secs
            self.event("phase_start", f"phase={i} rate={rate} secs={secs}")
            while not self.stop.is_set():
                left = int(round(end - time.time()))
                if left < 3:
                    break
                rc = self.run_once(i, rate, left)
                if self.stop.is_set() or end - time.time() < 3:
                    break
                self.event("iperf_exit_early", f"rc={rc} left={int(end - time.time())}s (outage? retrying in 2 s)")
                time.sleep(2)
            self.event("phase_end", f"phase={i}")
        self.raw.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--find", action="store_true", help="list UE IPs from the SMF log and exit")
    ap.add_argument("--ue", action="append", default=[], metavar="NAME=IP", help="e.g. A=10.45.0.2 (repeat for 2 UEs)")
    ap.add_argument("--duration", type=int, default=60, help="seconds (ignored with --phases)")
    ap.add_argument("--rate", default="0", help="iperf3 -b: 0 = as fast as TCP goes (default), or 15M, 4M...")
    ap.add_argument("--phases", help="RATE:SECONDS,... back to back, e.g. 4M:180,12M:360 (E4)")
    ap.add_argument("--parallel", type=int, default=1, help="iperf3 -P (4 if one TCP flow can't fill the cell)")
    ap.add_argument("--port", type=int, default=5201)
    ap.add_argument("--label", default="run")
    a = ap.parse_args()

    if a.find:
        find_ues()
        return
    if not a.ue:
        ap.error("give at least one --ue NAME=IP (see --find)")

    phases = parse_phases(a)
    outdir = os.path.join(REPO, "lab-data", "ue-traffic", f"{datetime.now():%Y%m%d-%H%M%S}_{a.label}")
    os.makedirs(outdir, exist_ok=True)
    total = sum(s for _, s in phases)
    log(f"[ue-traffic] {len(a.ue)} UE(s), {total} s, phases {phases}, -P {a.parallel} -> {outdir}")

    stop = threading.Event()
    runners = []
    for spec in a.ue:
        name, ip = spec.split("=", 1)
        r = Runner(name.strip(), ip.strip(), phases, a, outdir, stop)
        runners.append(r)
        r.start()

    try:
        while any(r.is_alive() for r in runners):
            time.sleep(1)
            now = time.time()
            parts = []
            for r in runners:
                v = r.last.get("v") if now - r.last.get("t", 0) < 3 else None
                parts.append(f"{r.name_}={v:7.1f}" if v is not None else f"{r.name_}=   --  ")
            log(f"{datetime.now():%H:%M:%S}  " + "  ".join(parts) + "  Mbps (sent)")
    except KeyboardInterrupt:
        log("[ue-traffic] Ctrl+C: stopping")
        stop.set()
        # only our clients: the compose iperf servers use the same image
        subprocess.run("docker ps -q --filter label=minas-ue-traffic=1 | xargs -r docker kill",
                       shell=True, capture_output=True)
        for r in runners:
            r.join(timeout=10)
    log(f"[ue-traffic] done -> {outdir}")


if __name__ == "__main__":
    sys.exit(main())
