"""
SLA monitor for the MINAS Orchestrator.

TCC I, UC2: "during the active window, both agents continuously report the
SLA state to the orchestrator". The domain agents already write their
telemetry (core_kpis / ran_kpis, via the collector), so this reads it
directly — deterministically, no LLM — instead of asking each domain's ReAct
loop for a check_sla every few seconds.

Every SLA_SAMPLE_SECONDS, for each intent IN FORCE (applied/degraded, window
open or no window), it stores one sla_samples row: the slice's latest
observed DL throughput vs. the intent's current guarantee (GBR of its active
policy, else its target). That table is what the SLA compliance rate of
Section 5.4 is computed from (agents/benchmark/sla_report.py).

After SLA_VIOLATION_STREAK consecutive non-compliant samples it records an
`sla_violation` event (once per SLA_VIOLATION_COOLDOWN_SECONDS per intent)
and logs a WARNING — the operator-facing notification.

Caveat (also in sla_report.py): "observed < target" is only a violation if
there was demand for the target. With no offered load at that rate (no
iperf at the target in the lab, or the mock/synthetic collector), low
observed throughput means an idle slice, not a broken guarantee.

A sample is only taken when the telemetry is fresh (newest core_kpis row for
the slice younger than SLA_MAX_TELEMETRY_AGE_SECONDS); no data is not
counted as non-compliance.

Env: SLA_MONITOR_ENABLED (true), SLA_SAMPLE_SECONDS (30),
     SLA_VIOLATION_STREAK (3), SLA_VIOLATION_COOLDOWN_SECONDS (600),
     SLA_MAX_TELEMETRY_AGE_SECONDS (60)
"""

from __future__ import annotations

import json
import os
import threading
import time

from db import get_db_conn

ENABLED = os.getenv("SLA_MONITOR_ENABLED", "true").strip().lower() not in ("false", "0", "no")
SAMPLE_SECONDS = int(os.getenv("SLA_SAMPLE_SECONDS", "30"))
STREAK = int(os.getenv("SLA_VIOLATION_STREAK", "3"))
COOLDOWN_SECONDS = int(os.getenv("SLA_VIOLATION_COOLDOWN_SECONDS", "600"))
MAX_AGE_SECONDS = int(os.getenv("SLA_MAX_TELEMETRY_AGE_SECONDS", "60"))


def in_force() -> list[tuple[int, int, float]]:
    """(intent_id, sst, target_mbps) for every intent currently in force."""
    with get_db_conn().cursor() as cur:
        cur.execute(
            """
            SELECT i.id, i.sst, COALESCE(p.gbr_dl_mbps, i.target_thp_mbps)
              FROM intents i
         LEFT JOIN LATERAL (
                   SELECT gbr_dl_mbps FROM policies
                    WHERE intent_id = i.id AND status = 'active'
                 ORDER BY applied_at DESC LIMIT 1
                   ) p ON TRUE
             WHERE i.status IN ('applied', 'degraded')
               AND (i.window_start IS NULL OR i.window_start <= NOW())
               AND (i.window_end   IS NULL OR i.window_end   >  NOW())
               AND COALESCE(p.gbr_dl_mbps, i.target_thp_mbps) IS NOT NULL
            """
        )
        return cur.fetchall()


def _observed(sst: int) -> float | None:
    with get_db_conn().cursor() as cur:
        cur.execute(
            """
            SELECT thp_dl_mbps FROM core_kpis
             WHERE sst = %s AND thp_dl_mbps IS NOT NULL
               AND collected_at > NOW() - make_interval(secs => %s)
          ORDER BY collected_at DESC LIMIT 1
            """,
            (sst, MAX_AGE_SECONDS),
        )
        row = cur.fetchone()
    return float(row[0]) if row else None


def sample_one(intent_id: int, sst: int, target: float) -> bool | None:
    """Store one sample; returns compliance, or None when there is no fresh telemetry."""
    observed = _observed(sst)
    if observed is None:
        return None
    compliant = observed >= target
    conn = get_db_conn()
    with conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sla_samples (intent_id, sst, target_thp_mbps, observed_thp_mbps, compliant) "
            "VALUES (%s, %s, %s, %s, %s)",
            (intent_id, sst, target, observed, compliant),
        )
    if not compliant:
        _maybe_violation(intent_id, sst, target, observed)
    return compliant


def _maybe_violation(intent_id: int, sst: int, target: float, observed: float) -> None:
    with get_db_conn().cursor() as cur:
        cur.execute(
            "SELECT compliant FROM sla_samples WHERE intent_id = %s ORDER BY sampled_at DESC, id DESC LIMIT %s",
            (intent_id, STREAK),
        )
        last = [r[0] for r in cur.fetchall()]
        if len(last) < STREAK or any(last):
            return
        cur.execute(
            """
            SELECT 1 FROM events
             WHERE intent_id = %s AND type = 'sla_violation'
               AND created_at > NOW() - make_interval(secs => %s)
             LIMIT 1
            """,
            (intent_id, COOLDOWN_SECONDS),
        )
        if cur.fetchone():
            return
    conn = get_db_conn()
    with conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO events (source, type, intent_id, sst, payload) "
            "VALUES ('sla-monitor', 'sla_violation', %s, %s, %s)",
            (intent_id, sst, json.dumps({"target_thp_mbps": target, "observed_thp_mbps": observed,
                                         "consecutive_samples": STREAK})),
        )
    print(f"[sla-monitor] WARNING operator: intent {intent_id} (SST={sst}) below its SLA for "
          f"{STREAK} samples in a row — observed {observed:.2f} < target {target} Mbps")


def _loop() -> None:
    print(f"[sla-monitor] started: every {SAMPLE_SECONDS}s, violation after {STREAK} "
          f"consecutive non-compliant samples")
    while True:
        try:
            for intent_id, sst, target in in_force():
                sample_one(intent_id, sst, float(target))
        except Exception as exc:  # a bad poll must not kill the thread
            print(f"[sla-monitor] poll error: {exc}")
        time.sleep(SAMPLE_SECONDS)


def start() -> threading.Thread | None:
    if not ENABLED:
        print("[sla-monitor] disabled (SLA_MONITOR_ENABLED=false)")
        return None
    thread = threading.Thread(target=_loop, name="sla-monitor", daemon=True)
    thread.start()
    return thread
