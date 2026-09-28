"""
Proactive slice monitor for the CN-NSSMF (Use Case 1).

TCC I, UC1: "the CN-NSSMF periodically queries the NWDAF ... when the
prediction exceeds the configured threshold, the CN-NSSMF reports to the
orchestrator, which decides and triggers the expansion of the slice's
resources". This is that loop. Detection is deterministic (no LLM): every
UC1_POLL_SECONDS it asks the NWDAF for SLICE_LOAD_LEVEL on each monitored
slice and compares the predicted demand with what the slice is guaranteed
right now. The DECISION stays with the orchestrator (LLM), reached through
POST {ORCHESTRATOR_URL}/event — the domain agent reports, it does not act on
its own.

What is monitored: per slice in UC1_SLICES, the newest intent that is in
force and has no window (a standing "keep it ready" intent, not a scheduled
one). Its guarantee is the GBR of its active policy (falling back to the
intent's target).

Trigger: predicted_thp > guarantee * (1 + UC1_MARGIN), with a prediction
that actually came from the model (source random_forest) — a mock/stale
answer never triggers. At most one event per intent every
UC1_COOLDOWN_SECONDS, checked against the events table so a restart doesn't
reset it.

Env: UC1_ENABLED (true), UC1_POLL_SECONDS (30), UC1_SLICES ("2"),
     UC1_MARGIN (0.10), UC1_COOLDOWN_SECONDS (300), UC1_HORIZON_SECONDS (60),
     ORCHESTRATOR_URL (http://orchestrator:8000)
"""

from __future__ import annotations

import json
import os
import threading
import time

import requests

from db import get_db_conn
from nwdaf_client import get_analytics

ENABLED = os.getenv("UC1_ENABLED", "true").strip().lower() not in ("false", "0", "no")
POLL_SECONDS = int(os.getenv("UC1_POLL_SECONDS", "30"))
SLICES = [int(s) for s in os.getenv("UC1_SLICES", "2").split(",") if s.strip()]
MARGIN = float(os.getenv("UC1_MARGIN", "0.10"))
COOLDOWN_SECONDS = int(os.getenv("UC1_COOLDOWN_SECONDS", "300"))
HORIZON_SECONDS = int(os.getenv("UC1_HORIZON_SECONDS", "60"))
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8000")


def watched() -> list[tuple[int, int, float | None]]:
    """(intent_id, sst, guarantee_mbps) — newest in-force windowless intent per slice."""
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (i.sst) i.id, i.sst,
                   COALESCE(p.gbr_dl_mbps, i.target_thp_mbps)
              FROM intents i
         LEFT JOIN LATERAL (
                   SELECT gbr_dl_mbps FROM policies
                    WHERE intent_id = i.id AND status = 'active'
                 ORDER BY applied_at DESC LIMIT 1
                   ) p ON TRUE
             WHERE i.sst = ANY(%s)
               AND i.status IN ('applied', 'degraded')
               AND i.window_end IS NULL
          ORDER BY i.sst, i.received_at DESC
            """,
            (SLICES,),
        )
        return cur.fetchall()


def _in_cooldown(intent_id: int) -> bool:
    with get_db_conn().cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM events
             WHERE intent_id = %s AND type = 'predicted_exhaustion'
               AND created_at > NOW() - make_interval(secs => %s)
             LIMIT 1
            """,
            (intent_id, COOLDOWN_SECONDS),
        )
        return cur.fetchone() is not None


def _record_event(intent_id: int, sst: int, payload: dict) -> int:
    conn = get_db_conn()
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO events (source, type, intent_id, sst, payload)
            VALUES ('cn-nssmf', 'predicted_exhaustion', %s, %s, %s)
         RETURNING id
            """,
            (intent_id, sst, json.dumps(payload)),
        )
        return cur.fetchone()[0]


def _mark_undelivered(event_id: int, reason: str) -> None:
    conn = get_db_conn()
    with conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE events SET handled_at = NOW(), outcome = 'undelivered', "
            "payload = payload || jsonb_build_object('delivery_error', %s::text) WHERE id = %s",
            (reason, event_id),
        )


def check_one(intent_id: int, sst: int, guarantee: float | None) -> dict | None:
    """Evaluate one watched intent; returns the event sent, or None."""
    if guarantee is None:
        return None
    a = get_analytics("SLICE_LOAD_LEVEL", sst, HORIZON_SECONDS)
    if a.get("source") != "random_forest":
        print(f"[cn-nssmf monitor] sst={sst}: no model prediction ({a.get('reason') or a.get('source')}) — skip")
        return None
    predicted = float(a["predicted_thp_mbps"])
    if predicted <= guarantee * (1 + MARGIN):
        return None
    if _in_cooldown(intent_id):
        print(f"[cn-nssmf monitor] intent {intent_id}: predicted {predicted} > {guarantee} but in cooldown")
        return None

    event = {
        "type": "predicted_exhaustion",
        "intent_id": intent_id,
        "sst": sst,
        "predicted_thp_mbps": round(predicted, 2),
        "guaranteed_mbps": guarantee,
        "predicted_load": a.get("predicted_load"),
        "horizon_seconds": HORIZON_SECONDS,
    }
    event["event_id"] = _record_event(intent_id, sst, event)
    print(f"[cn-nssmf monitor] intent {intent_id} sst={sst}: NWDAF predicts {predicted:.2f} Mbps "
          f"> guarantee {guarantee} (+{MARGIN:.0%}) — reporting to orchestrator (event {event['event_id']})")
    try:
        resp = requests.post(f"{ORCHESTRATOR_URL}/event", json=event, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[cn-nssmf monitor] event {event['event_id']} not delivered: {exc}")
        _mark_undelivered(event["event_id"], str(exc))
    return event


def _loop() -> None:
    print(f"[cn-nssmf monitor] UC1 monitor started: slices={SLICES} every {POLL_SECONDS}s, "
          f"margin={MARGIN:.0%}, cooldown={COOLDOWN_SECONDS}s")
    while True:
        try:
            for intent_id, sst, guarantee in watched():
                check_one(intent_id, sst, guarantee)
        except Exception as exc:  # a bad poll must not kill the thread
            print(f"[cn-nssmf monitor] poll error: {exc}")
        time.sleep(POLL_SECONDS)


def start() -> threading.Thread | None:
    if not ENABLED:
        print("[cn-nssmf monitor] UC1 monitor disabled (UC1_ENABLED=false)")
        return None
    thread = threading.Thread(target=_loop, name="uc1-monitor", daemon=True)
    thread.start()
    return thread
