#!/usr/bin/env python3
"""MINAS lab — saturating DOWNLINK load to the UEs with nothing installed on them.

Sends paced UDP from inside the UPF's network namespace to each UE's IP, so the
packets take the real path UPF -> GTP-U -> gNB -> radio. The phone simply drops
them (closed port), which is fine: what matters is that the gNB has more to
send than the cell can carry, so its scheduler must split the PRBs between the
UEs. The delivered throughput per UE is then read from the gNB console trace
(gnb_restart.sh puts a timestamp on each line; agents/benchmark/lab_analysis.py
--e2 does the analysis). The UPF counters show the OFFERED load, not what was
delivered.

Needs root (it enters the UPF's netns with nsenter) and docker. Stdlib only.

    sudo python3 scripts/udp_load.py --find
    sudo python3 scripts/udp_load.py --ue A=imsi-001010000000001/internet \
                                     --ue B=imsi-001010000000002/slice2 \
                                     --rate 600M --duration 1200 --label e2s-rep1

  --ue NAME=IMSI/DNN  the UE's current IP is read from `docker logs smf` and
                      refreshed every 5 s, so traffic follows the UE when it
                      re-attaches with a new IP after a gNB restart.
  --ue NAME=IP        fixed IP (no refresh).
  --mark B            this UE pauses for 2 s at the start of every minute
                      (second 0 and 1). That lets the analysis tell which RNTI
                      in the gNB trace is which UE, since RNTIs change at every
                      restart and the trace does not show the slice. Default:
                      the last --ue. --mark none turns it off.

Output: lab-data/ue-traffic/<YYYYmmdd-HHMMSS>_<label>/
    sent_<ue>.csv   epoch, iso, ip, Mbps sent in that second
    events.csv      start, IP changes, end
scripts/lab_collect.sh copies the folder into the run's ue/.
"""
from __future__ import annotations

import argparse
import csv
import multiprocessing as mp
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SMF_UE = re.compile(r"SUPI\[(imsi-\d+)\]\s+DNN\[([^\]]*)\]\s+IPv4\[([\d.]+)\]")
UNIT = {"": 1, "K": 1e3, "M": 1e6, "G": 1e9}


def upf_pid() -> str:
    out = subprocess.run(["docker", "inspect", "-f", "{{.State.Pid}}", "upf"],
                         capture_output=True, text=True)
    pid = out.stdout.strip()
    if out.returncode != 0 or not pid or pid == "0":
        sys.exit("upf container not running (docker inspect upf)")
    return pid


def enter_upf_netns() -> None:
    """Re-exec this script inside the UPF's network namespace (once)."""
    if os.environ.get("MINAS_IN_UPF_NS") == "1":
        return
    if os.geteuid() != 0:
        sys.exit("run with sudo: it needs nsenter into the UPF's network namespace")
    pid = upf_pid()
    os.execvp("nsenter", ["nsenter", "-t", pid, "-n", "env", "MINAS_IN_UPF_NS=1",
                          sys.executable, os.path.abspath(__file__), *sys.argv[1:]])


def smf_sessions() -> dict[tuple[str, str], str]:
    out = subprocess.run(["docker", "logs", "smf"], capture_output=True, text=True)
    last = {}
    for line in (out.stdout + out.stderr).splitlines():
        m = SMF_UE.search(line)
        if m:
            last[(m.group(1), m.group(2))] = m.group(3)
    return last


def find() -> None:
    s = smf_sessions()
    if not s:
        print("no UE session in `docker logs smf` yet (UEs attached? stack up?)")
        return
    for (supi, dnn), ip in sorted(s.items()):
        sl = "SST 1" if ip.startswith("10.45.") else "SST 2" if ip.startswith("10.46.") else "?"
        print(f"{supi}  DNN={dnn:9}  IPv4={ip:15}  {sl}   -> --ue X={supi}/{dnn}")
    print("(latest session per SUPI/DNN; after a re-attach the newest IP is shown)")


def rate_bps(s: str) -> float:
    m = re.fullmatch(r"([\d.]+)([KMG]?)", s.strip().upper())
    if not m:
        raise argparse.ArgumentTypeError(f"bad rate {s!r} (e.g. 600M)")
    return float(m.group(1)) * UNIT[m.group(2)]


def sender(name, ip_arr, rate, size, port, mark, stop, out_dir):
    """One process per UE: paced UDP to the current IP, 1 ms ticks."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
    buf = os.urandom(size)
    per_tick = rate * 0.001 / (size * 8)
    f = open(os.path.join(out_dir, f"sent_{name}.csv"), "w", newline="")
    w = csv.writer(f)
    w.writerow(["t_epoch", "t_iso", "ue", "ip", "mbps_sent"])
    acc, sent, sec = 0.0, 0, int(time.time())
    nxt = time.perf_counter()
    while not stop.is_set():
        now = time.time()
        if int(now) != sec:
            ip = ip_arr.value.decode()
            w.writerow([sec, datetime.fromtimestamp(sec).isoformat(), name, ip,
                        f"{sent * size * 8 / 1e6:.1f}"])
            f.flush()
            sec, sent = int(now), 0
        ip = ip_arr.value.decode()
        paused = mark and int(now) % 60 in (0, 1)
        acc += per_tick
        n = int(acc)
        acc -= n
        if ip and not paused:
            for _ in range(n):
                try:
                    sock.sendto(buf, (ip, port))
                    sent += 1
                except OSError:
                    break  # buffer full / no route: drop this tick
        nxt += 0.001
        d = nxt - time.perf_counter()
        if d > 0:
            time.sleep(d)
        elif d < -0.2:
            nxt = time.perf_counter()  # fell behind (CPU): don't burst to catch up
    f.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--find", action="store_true", help="list UE sessions from the SMF log and exit")
    ap.add_argument("--ue", action="append", default=[], metavar="NAME=IMSI/DNN|NAME=IP")
    ap.add_argument("--rate", type=rate_bps, default=rate_bps("600M"), help="per UE (default 600M)")
    ap.add_argument("--size", type=int, default=1300, help="UDP payload bytes (default 1300, fits GTP in 1500)")
    ap.add_argument("--port", type=int, default=9)
    ap.add_argument("--duration", type=int, default=1200, help="seconds (default 1200)")
    ap.add_argument("--mark", default=None, help="UE that pauses 2 s every minute (default: last --ue; 'none')")
    ap.add_argument("--label", default="udp")
    a = ap.parse_args()

    enter_upf_netns()
    if a.find:
        find()
        return
    if not a.ue:
        ap.error("give at least one --ue (see --find)")

    ues = []
    for spec in a.ue:
        name, _, who = spec.partition("=")
        if re.fullmatch(r"[\d.]+", who):
            ues.append((name, None, None, who))
        else:
            imsi, _, dnn = who.partition("/")
            ues.append((name, imsi, dnn or None, ""))
    mark = a.mark if a.mark is not None else ues[-1][0]

    out = os.path.join(REPO, "lab-data", "ue-traffic", f"{datetime.now():%Y%m%d-%H%M%S}_{a.label}")
    os.makedirs(out, exist_ok=True)
    ev = open(os.path.join(out, "events.csv"), "w", newline="")
    evw = csv.writer(ev)
    evw.writerow(["t_epoch", "t_iso", "ue", "event", "detail"])

    def event(ue, kind, detail=""):
        t = time.time()
        evw.writerow([f"{t:.3f}", datetime.fromtimestamp(t).isoformat(timespec="seconds"), ue, kind, detail])
        ev.flush()
        print(f"{datetime.now():%H:%M:%S}  {ue:3} {kind} {detail}", flush=True)

    stop = mp.Event()
    procs, arrs = [], {}
    for name, imsi, dnn, ip in ues:
        arr = mp.Array("c", 16)
        arr.value = ip.encode()
        arrs[name] = (arr, imsi, dnn)
        p = mp.Process(target=sender, args=(name, arr, a.rate, a.size, a.port, name == mark, stop, out),
                       daemon=True)
        procs.append(p)
    event("-", "start", f"rate={a.rate / 1e6:.0f}M/UE size={a.size} mark={mark} duration={a.duration}s")
    for p in procs:
        p.start()

    t_end = time.time() + a.duration
    last_print = 0.0
    try:
        while time.time() < t_end:
            sess = smf_sessions() if any(i for _, i, _ in arrs.values()) else {}
            for name, (arr, imsi, dnn) in arrs.items():
                if not imsi:
                    continue
                cands = [ip for (s, d), ip in sess.items() if s == imsi and (dnn is None or d == dnn)]
                new = cands[-1] if cands else ""
                if new != arr.value.decode():
                    event(name, "ip", f"{arr.value.decode() or '-'} -> {new or '-'} ({imsi}/{dnn})")
                    arr.value = new.encode()
            if time.time() - last_print > 30:
                ips = "  ".join(f"{n}={a_.value.decode() or '--'}" for n, (a_, _, _) in arrs.items())
                print(f"{datetime.now():%H:%M:%S}  sending {a.rate / 1e6:.0f}M/UE  {ips}  "
                      f"(delivered: see the gNB console)", flush=True)
                last_print = time.time()
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    stop.set()
    for p in procs:
        p.join(timeout=3)
    event("-", "end")
    print("saved:", out)


if __name__ == "__main__":
    main()
