"""
Backfill core_kpis / ran_kpis with synthetic history (synth.py) ending now.

For RQ4 and for testing the NWDAF / Use Case 1 without waiting days for the
live collector. Meant for a DEDICATED database (e.g. minas_synth), so the
synthetic series never mixes with real or benchmark telemetry:

    docker exec postgres psql -U minas -d minas -c "CREATE DATABASE minas_synth"
    docker exec -i postgres psql -U minas -d minas_synth < agents/schema.sql
    POSTGRES_HOST=localhost POSTGRES_DB=minas_synth \\
        python agents/collector/backfill_synthetic.py --days 7
    POSTGRES_HOST=localhost POSTGRES_DB=minas_synth NWDAF_HISTORY_LIMIT=20000 \\
        python agents/nwdaf/benchmark_rq4.py

Refuses to write into a database that already has KPI rows unless --append
(continue a synthetic series) or --truncate (wipe core_kpis/ran_kpis first).
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from psycopg2.extras import execute_values  # noqa: E402

import synth  # noqa: E402
from db import get_db_conn  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--interval", type=float, default=10.0, help="seconds between samples")
    ap.add_argument("--slices", default="1,2")
    ap.add_argument("--seed", type=int, default=0)
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--append", action="store_true")
    group.add_argument("--truncate", action="store_true")
    args = ap.parse_args()

    conn = get_db_conn()
    db = conn.info.dbname
    with conn.cursor() as cur:
        cur.execute("SELECT (SELECT COUNT(*) FROM core_kpis) + (SELECT COUNT(*) FROM ran_kpis)")
        existing = cur.fetchone()[0]
    if existing and not (args.append or args.truncate):
        sys.exit(f"database '{db}' already has {existing} KPI rows — use a dedicated database, "
                 "or pass --append / --truncate explicitly")
    if args.truncate:
        with conn, conn.cursor() as cur:
            cur.execute("TRUNCATE core_kpis, ran_kpis")
        print(f"[backfill] truncated core_kpis, ran_kpis in '{db}'")

    slices = [int(s) for s in args.slices.split(",")]
    n = int(args.days * 86400 / args.interval)
    end = datetime.now(timezone.utc)
    times = [end - timedelta(seconds=args.interval * (n - 1 - k)) for k in range(n)]
    rng = random.Random(args.seed)

    core_rows, ran_rows = [], []
    for sst in slices:
        load = synth.SliceLoad(sst, args.seed)
        for t in times:
            thp = load.sample(t)
            c = synth.core_row(sst, thp, rng)
            r = synth.ran_row(sst, thp, rng)
            core_rows.append((t, sst, c["ues_registered"], c["pdu_sessions"], c["thp_dl_mbps"], c["thp_ul_mbps"]))
            ran_rows.append((t, r["ue_id"], sst, r["rsrp_dbm"], r["sinr_db"], r["mcs_dl"], r["mcs_ul"],
                             r["prb_used_dl"], r["prb_used_ul"], r["thp_dl_mbps"], r["thp_ul_mbps"]))

    with conn, conn.cursor() as cur:
        execute_values(cur, "INSERT INTO core_kpis (collected_at, sst, ues_registered, pdu_sessions, "
                            "thp_dl_mbps, thp_ul_mbps) VALUES %s", core_rows, page_size=5000)
        execute_values(cur, "INSERT INTO ran_kpis (collected_at, ue_id, sst, rsrp_dbm, sinr_db, mcs_dl, "
                            "mcs_ul, prb_used_dl, prb_used_ul, thp_dl_mbps, thp_ul_mbps) VALUES %s",
                       ran_rows, page_size=5000)
    print(f"[backfill] '{db}': {n} samples x {len(slices)} slices "
          f"({args.days} days @ {args.interval:g}s, seed {args.seed}), ending {end.isoformat()}")


if __name__ == "__main__":
    main()
