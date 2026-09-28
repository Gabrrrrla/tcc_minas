"""
SLA compliance rate (TCC-II-DRAFT.tex Section 5.4) from the SLA monitor's
samples (agents/orchestrator/sla_monitor.py -> sla_samples).

  compliance(intent) = compliant samples / samples taken while it was in force

Also reports the sla_violation events raised for each intent.

Read the caveat before quoting a number: a sample is "compliant" when the
slice's observed DL throughput >= the intent's guarantee. That only measures
the guarantee when there WAS demand at that rate — in the lab, drive traffic
at (or above) the target with iperf during the window; with the mock or
synthetic collector, low throughput just means an idle slice, so the rate
here says nothing about the network.

Usage (host, Postgres published on localhost:5432):
    POSTGRES_HOST=localhost python agents/benchmark/sla_report.py
    POSTGRES_HOST=localhost python agents/benchmark/sla_report.py --intent-ids 12,13 --out results/sla.jsonl
    POSTGRES_HOST=localhost python agents/benchmark/sla_report.py --since 2026-10-01T18:00:00-03:00
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db_conn  # noqa: E402
from timeutil import parse_iso  # noqa: E402


def report(intent_ids: list[int] | None = None, since: str | None = None) -> list[dict]:
    where, args = [], []
    if intent_ids:
        where.append("s.intent_id = ANY(%s)")
        args.append(intent_ids)
    if since:
        where.append("s.sampled_at >= %s")
        args.append(parse_iso(since))
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with get_db_conn().cursor() as cur:
        cur.execute(
            f"""
            SELECT s.intent_id, s.sst, COUNT(*),
                   COUNT(*) FILTER (WHERE s.compliant),
                   MIN(s.sampled_at), MAX(s.sampled_at),
                   AVG(s.observed_thp_mbps), AVG(s.target_thp_mbps),
                   (SELECT COUNT(*) FROM events e
                     WHERE e.intent_id = s.intent_id AND e.type = 'sla_violation')
              FROM sla_samples s
              {clause}
          GROUP BY s.intent_id, s.sst
          ORDER BY s.intent_id
            """,
            args,
        )
        rows = cur.fetchall()
    return [
        {
            "intent_id": r[0], "sst": r[1], "samples": r[2], "compliant": r[3],
            "compliance_rate": round(r[3] / r[2], 4) if r[2] else None,
            "first_sample": r[4].isoformat(), "last_sample": r[5].isoformat(),
            "mean_observed_mbps": round(float(r[6]), 3), "mean_target_mbps": round(float(r[7]), 3),
            "sla_violation_events": r[8],
        }
        for r in rows
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--intent-ids", default=None, help="comma-separated intent ids")
    ap.add_argument("--since", default=None, help="ISO-8601; naive = MINAS_TZ")
    ap.add_argument("--out", default=None, help="write one JSON line per intent")
    args = ap.parse_args()

    ids = [int(x) for x in args.intent_ids.split(",")] if args.intent_ids else None
    rows = report(ids, args.since)
    if not rows:
        print("no SLA samples for that selection (is the orchestrator's SLA monitor running?)")
        return

    print(f"{'intent':>6} {'sst':>3} {'samples':>7} {'compliance':>10} {'obs Mbps':>9} {'tgt Mbps':>9} {'viol.':>5}")
    for r in rows:
        print(f"{r['intent_id']:>6} {r['sst']:>3} {r['samples']:>7} {r['compliance_rate']:>10.1%} "
              f"{r['mean_observed_mbps']:>9.2f} {r['mean_target_mbps']:>9.2f} {r['sla_violation_events']:>5}")
    total = sum(r["samples"] for r in rows)
    ok = sum(r["compliant"] for r in rows)
    print(f"\npooled compliance: {ok}/{total} = {ok / total:.1%}  (see the demand caveat in this script's docstring)")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
