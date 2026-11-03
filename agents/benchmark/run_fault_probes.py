"""
Fault-injection probes for the window lifecycle (Use Case 2).

The window probes (run_window_probes.py) only exercise the happy path. These
probes check the claims about cross-domain consistency when one domain fails:
"if one domain fails, the other is rolled back and the intent is marked
failed" and "reversion is retried until both domains confirm".

Each probe is a real natural-language intent with a short window a few minutes
ahead (SST=1, 8 Mbps, feasible), with a fault injected in the RAN domain:

  ran_fails    the RAN-NSSMF answers, but its agent cannot act: the container
               is recreated with a model that makes no tool calls
               (OTel-LLM-E4B-IT, 0 tool calls in 240 benchmark executions),
               so apply_resources returns the structured status 'failed'.
               Expected: CN policy rolled back, intent 'failed', nothing active.
  ran_down     the RAN-NSSMF is unreachable for the whole window (container
               stopped before window_start, restarted after window_end).
               Expected (claim): nothing left in force after the window.
  ran_down_at_end
               the RAN-NSSMF is unreachable when the window closes (stopped
               after activation, restarted 60 s after window_end).
               Expected: reversion retried and confirmed after the restart.

Nothing in the system is changed: faults are injected with docker only.

Usage (stack up, from the repo root):
    POSTGRES_HOST=localhost python agents/benchmark/run_fault_probes.py --reps 2
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from db import get_db_conn  # noqa: E402
from run_window_probes import _state  # noqa: E402
from timeutil import now  # noqa: E402

SCENARIOS = ["ran_fails", "ran_down", "ran_down_at_end"]
FAIL_MODEL = "otel-llm-e4b-it:latest"


def sh(*cmd: str, env: dict | None = None) -> None:
    r = subprocess.run(list(cmd), cwd=REPO, env={**os.environ, **(env or {})},
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {r.stderr.strip()}")


def recreate_ran(model: str | None) -> None:
    env = {"UC1_ENABLED": "true", "SLA_MONITOR_ENABLED": "true"}
    if model:
        env["MINAS_MODEL"] = model
    sh("docker", "compose", "up", "-d", "--force-recreate", "--no-deps", "ran-nssmf", env=env)
    wait_health(8002)


def wait_health(port: int, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if requests.get(f"http://localhost:{port}/health", timeout=3).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    raise RuntimeError(f"service on :{port} not healthy")


def sleep_until(t: datetime) -> None:
    while (d := (t - now()).total_seconds()) > 0:
        time.sleep(min(d, 5))


def snapshot(conn, intent_id: int) -> dict:
    st = _state(conn, intent_id)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM policies WHERE intent_id = %s ORDER BY id", (intent_id,))
        cn = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT status FROM ran_allocations WHERE intent_id = %s ORDER BY id", (intent_id,))
        ran = [r[0] for r in cur.fetchall()]
    return {"intent_status": st["status"], "cn_rows": cn, "ran_rows": ran,
            "active_left": st["active_left"],
            "cn_applied_at": [a.isoformat() for a in st["cn_applied"]],
            "ran_applied_at": [a.isoformat() for a in st["ran_applied"]],
            "reverted_at": [a.isoformat() for a in st["reverted"]]}


def run_one(conn, scenario: str, rep: int, host: str, lead_min: float, dur_min: int) -> dict:
    start = (now() + timedelta(minutes=lead_min)).replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = start + timedelta(minutes=dur_min)
    text = (f"Guarantee 8 Mbps on the eMBB slice (SST=1) from {start:%H:%M} to "
            f"{end:%H:%M} today ({start:%Y-%m-%d}).")
    print(f"\n[{scenario} #{rep}] {text}")
    log: list[str] = []

    if scenario == "ran_fails":
        recreate_ran(FAIL_MODEL)
        log.append(f"{now():%H:%M:%S} ran-nssmf recreated with {FAIL_MODEL}")

    body = requests.post(f"{host}/intent", json={"intent": text}, timeout=180).json()
    ids = body.get("intent_ids") or []
    if not ids:
        if scenario == "ran_fails":
            recreate_ran(None)
        return {"scenario": scenario, "rep": rep, "intent_id": None, "error": "not recorded",
                "outcome": str(body.get("outcome"))[:200]}
    iid = ids[0]
    recorded = _state(conn, iid)["status"]
    print(f"[{scenario} #{rep}] intent {iid} recorded as '{recorded}'")

    observed: list[tuple[str, dict]] = []

    def observe(label: str) -> None:
        s = snapshot(conn, iid)
        observed.append((label, s))
        print(f"[{scenario} #{rep}] {label}: {s['intent_status']} cn={s['cn_rows']} ran={s['ran_rows']}")

    if scenario == "ran_fails":
        sleep_until(start + timedelta(minutes=2))
        observe("window+2min")
        sleep_until(end + timedelta(seconds=60))
        observe("end+60s")
        recreate_ran(None)
        log.append(f"{now():%H:%M:%S} ran-nssmf restored")
    elif scenario == "ran_down":
        sleep_until(start - timedelta(seconds=30))
        sh("docker", "stop", "ran-nssmf")
        log.append(f"{now():%H:%M:%S} ran-nssmf stopped")
        sleep_until(start + timedelta(minutes=1))
        observe("window+1min (RAN down)")
        sleep_until(end + timedelta(seconds=60))
        observe("end+60s (RAN down)")
        sh("docker", "start", "ran-nssmf")
        wait_health(8002)
        log.append(f"{now():%H:%M:%S} ran-nssmf started")
        sleep_until(end + timedelta(minutes=3))
        observe("end+3min (RAN back)")
    elif scenario == "ran_down_at_end":
        sleep_until(end - timedelta(seconds=30))
        observe("before fault")
        sh("docker", "stop", "ran-nssmf")
        log.append(f"{now():%H:%M:%S} ran-nssmf stopped")
        sleep_until(end + timedelta(seconds=55))
        observe("end+55s (RAN down)")
        sh("docker", "start", "ran-nssmf")
        wait_health(8002)
        log.append(f"{now():%H:%M:%S} ran-nssmf started")
        sleep_until(end + timedelta(minutes=3))
        observe("end+3min (RAN back)")

    final = snapshot(conn, iid)
    return {"scenario": scenario, "rep": rep, "intent_id": iid, "recorded_status": recorded,
            "window_start": start.isoformat(), "window_end": end.isoformat(),
            "fault_log": log, "observations": observed, "final": final}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="http://localhost:8000")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--lead-min", type=float, default=2.0)
    ap.add_argument("--duration-min", type=int, default=3)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    out = args.out or os.path.join(HERE, "results", "fault-probes",
                                   f"fault-probes_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    conn = get_db_conn()
    for rep in range(1, args.reps + 1):
        for sc in args.scenarios.split(","):
            try:
                r = run_one(conn, sc, rep, args.host, args.lead_min, args.duration_min)
            except Exception as exc:  # keep going; record the failure of the probe itself
                r = {"scenario": sc, "rep": rep, "error": repr(exc)}
                try:
                    subprocess.run(["docker", "start", "ran-nssmf"], capture_output=True)
                except Exception:
                    pass
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps(r, default=str) + "\n")
            print(f"[{sc} #{rep}] final: {r.get('final')}")
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
