"""
End-to-end check of the window lifecycle (Use Case 2): scheduled -> activated
at window_start -> reverted at window_end — the path the main benchmark no
longer exercises, since its windowed intents are days ahead and correctly
stay 'scheduled' (intent_set.py).

Each probe is a real natural-language intent with a SHORT window a few
minutes ahead, in operator-local time ("from 14:32 to 14:35 today"). Probes
are staggered so their windows don't overlap. After POSTing them all, the
runner polls the database until every probe is reverted/failed or the
deadline passes, and reports per probe:

  scheduled            recorded as 'scheduled' (not applied early)
  applied_early        a policy/allocation exists with applied_at < window_start
                       (must never happen)
  activation_delay_s   window_start -> BOTH domains applied (the slower of the
                       CN policy and the RAN allocation; each domain's first
                       application). cn_/ran_activation_delay_s separately.
  activated_status     applied / degraded / failed after activation
  reversion_delay_s    last reverted_at - window_end
  final_status         'reverted' expected
  reversion_correct    final_status == 'reverted' and nothing left active

This is also the "reversion correctness" metric of Section 5.4, which the
main runner could only report as not_due.

Usage (stack up with the current image, orchestrator on :8000):
    POSTGRES_HOST=localhost python agents/benchmark/run_window_probes.py --probes 4
Takes roughly lead + probes x (duration + gap) + grace minutes (~25 min default).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db_conn  # noqa: E402
from timeutil import now  # noqa: E402

TARGETS_MBPS = [8, 12, 16, 25]   # 25 > the 20.4 Mbps cell -> expected 'degraded'


def plan(n: int, lead_min: float, duration_min: int, gap_min: int, base: datetime | None = None) -> list[dict]:
    """Probe windows on whole minutes, local time, non-overlapping."""
    base = (base or now()) + timedelta(minutes=lead_min)
    base = base.replace(second=0, microsecond=0) + timedelta(minutes=1)
    probes = []
    for i in range(n):
        start = base + timedelta(minutes=i * (duration_min + gap_min))
        end = start + timedelta(minutes=duration_min)
        thp = TARGETS_MBPS[i % len(TARGETS_MBPS)]
        probes.append({
            "probe": i,
            "target_thp_mbps": float(thp),
            "window_start": start,
            "window_end": end,
            "text": (f"Guarantee {thp} Mbps on the eMBB slice (SST=1) from {start:%H:%M} to "
                     f"{end:%H:%M} today ({start:%Y-%m-%d})."),
        })
    return probes


def _state(conn, intent_id: int) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT status, window_start, window_end FROM intents WHERE id = %s", (intent_id,))
        status, ws, we = cur.fetchone()
        cur.execute(
            """
            SELECT 'cn', applied_at, reverted_at, status FROM policies WHERE intent_id = %s
             UNION ALL
            SELECT 'ran', applied_at, reverted_at, status FROM ran_allocations WHERE intent_id = %s
            """,
            (intent_id, intent_id),
        )
        rows = cur.fetchall()
    return {"status": status, "window_start": ws, "window_end": we,
            "cn_applied": [r[1] for r in rows if r[0] == "cn"],
            "ran_applied": [r[1] for r in rows if r[0] == "ran"],
            "reverted": [r[2] for r in rows if r[2]],
            "active_left": sum(1 for r in rows if r[3] == "active")}


def evaluate(p: dict, st: dict, activated_status: str | None) -> dict:
    ws, we = st["window_start"], st["window_end"]
    applied = st["cn_applied"] + st["ran_applied"]
    delay = lambda ts: round((min(ts) - ws).total_seconds(), 1) if ts and ws else None  # noqa: E731
    cn_d, ran_d = delay(st["cn_applied"]), delay(st["ran_applied"])
    return {
        "probe": p["probe"],
        "intent_id": p.get("intent_id"),
        "text": p["text"],
        "target_thp_mbps": p["target_thp_mbps"],
        "scheduled": p.get("recorded_status") == "scheduled",
        "window_start": ws.isoformat() if ws else None,
        "window_end": we.isoformat() if we else None,
        "window_matches": bool(ws and we and ws == p["window_start"] and we == p["window_end"]),
        "applied_early": any(a < ws for a in applied) if ws else None,
        "activation_delay_s": max(cn_d, ran_d) if cn_d is not None and ran_d is not None else None,
        "cn_activation_delay_s": cn_d,
        "ran_activation_delay_s": ran_d,
        "activated_status": activated_status,
        "reversion_delay_s": (round((max(st["reverted"]) - we).total_seconds(), 1)
                              if st["reverted"] and we else None),
        "final_status": st["status"],
        "reversion_correct": st["status"] == "reverted" and st["active_left"] == 0,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="http://localhost:8000")
    ap.add_argument("--probes", type=int, default=4)
    ap.add_argument("--lead-min", type=float, default=3.0, help="minutes before the first window opens")
    ap.add_argument("--duration-min", type=int, default=3)
    ap.add_argument("--gap-min", type=int, default=1)
    ap.add_argument("--grace-min", type=int, default=5, help="wait after the last window_end")
    ap.add_argument("--timeout", type=float, default=180.0, help="per /intent call")
    ap.add_argument("--condition", default="window-probes")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    probes = plan(args.probes, args.lead_min, args.duration_min, args.gap_min)
    conn = get_db_conn()

    for p in probes:
        print(f"[probe {p['probe']}] {p['text']}")
        try:
            body = requests.post(f"{args.host}/intent", json={"intent": p["text"]}, timeout=args.timeout).json()
        except requests.RequestException as exc:
            print(f"[probe {p['probe']}] HTTP error: {exc}")
            continue
        ids = body.get("intent_ids") or []
        if not ids:
            print(f"[probe {p['probe']}] no intent recorded — outcome: {str(body.get('outcome'))[:120]}")
            continue
        p["intent_id"] = ids[0]
        p["recorded_status"] = _state(conn, ids[0])["status"]
        print(f"[probe {p['probe']}] intent {ids[0]} recorded as '{p['recorded_status']}'")
        if p["window_start"] <= now():
            print(f"[probe {p['probe']}] WARNING: window already open after the /intent call "
                  f"— increase --lead-min")

    live = [p for p in probes if "intent_id" in p]
    activated: dict[int, str] = {}
    deadline = max(p["window_end"] for p in probes) + timedelta(minutes=args.grace_min)
    while live and now() < deadline:
        done = True
        for p in live:
            st = _state(conn, p["intent_id"])
            if st["status"] in ("applied", "degraded", "failed") and p["intent_id"] not in activated:
                activated[p["intent_id"]] = st["status"]
                print(f"[probe {p['probe']}] activated: {st['status']}")
            if st["status"] not in ("reverted", "failed"):
                done = False
        if done:
            break
        time.sleep(10)

    results = [evaluate(p, _state(conn, p["intent_id"]), activated.get(p["intent_id"])) for p in live]
    results += [{"probe": p["probe"], "text": p["text"], "intent_id": None, "final_status": "not_recorded"}
                for p in probes if "intent_id" not in p]

    out = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results",
                                   f"{args.condition}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps({**r, "condition": args.condition}) + "\n")

    print(f"\n{'probe':>5} {'sched':>5} {'early':>5} {'act.delay':>9} {'activated':>9} {'rev.delay':>9} {'final':>9}")
    for r in results:
        fmt = lambda v: "-" if v is None else (f"{v:.0f}s" if isinstance(v, float) else str(v))  # noqa: E731
        print(f"{r['probe']:>5} {fmt(r.get('scheduled')):>5} {fmt(r.get('applied_early')):>5} "
              f"{fmt(r.get('activation_delay_s')):>9} {fmt(r.get('activated_status')):>9} "
              f"{fmt(r.get('reversion_delay_s')):>9} {r['final_status']:>9}")
    ok = sum(1 for r in results if r.get("reversion_correct") and r.get("scheduled") and not r.get("applied_early"))
    print(f"\nlifecycle correct (scheduled, not early, reverted): {ok}/{len(results)}   -> {out}")


if __name__ == "__main__":
    main()
