"""
Shared PostgreSQL connection for MINAS agents.
Connection parameters come from environment variables:
  POSTGRES_HOST (default: postgres)
  POSTGRES_PORT (default: 5432)
  POSTGRES_DB   (default: minas)
  POSTGRES_USER (default: minas)
  POSTGRES_PASSWORD (default: minas)
"""

import os
import psycopg2

_conn = None


def get_db_conn():
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "postgres"),
            port=int(os.getenv("POSTGRES_PORT", 5432)),
            dbname=os.getenv("POSTGRES_DB", "minas"),
            user=os.getenv("POSTGRES_USER", "minas"),
            password=os.getenv("POSTGRES_PASSWORD", "minas"),
        )
    return _conn
