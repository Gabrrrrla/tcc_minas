"""
Shared PostgreSQL connection for MINAS agents.
Connection parameters come from environment variables:
  POSTGRES_HOST (default: postgres)
  POSTGRES_PORT (default: 5432)
  POSTGRES_DB   (default: minas)
  POSTGRES_USER (default: minas)
  POSTGRES_PASSWORD (default: minas)

One connection per THREAD (via threading.local), not a single global one.
The orchestrator runs the Flask request handler and the reversion scheduler
(scheduler.py) as separate threads in the same process; psycopg2/libpq
connections aren't safe for concurrent command dispatch from multiple
threads, so sharing one globally let the scheduler silently deadlock waiting
on a connection the Flask thread was mid-query on — no exception, no log
line, it just stopped reverting anything. Found + fixed 2026-09-12 while
running the first live smoke test (see runbook / HANDOVER for the repro).
"""

import os
import threading

import psycopg2

_local = threading.local()


def get_db_conn():
    conn = getattr(_local, "conn", None)
    if conn is None or conn.closed:
        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "postgres"),
            port=int(os.getenv("POSTGRES_PORT", 5432)),
            dbname=os.getenv("POSTGRES_DB", "minas"),
            user=os.getenv("POSTGRES_USER", "minas"),
            password=os.getenv("POSTGRES_PASSWORD", "minas"),
        )
        _local.conn = conn
    return conn
