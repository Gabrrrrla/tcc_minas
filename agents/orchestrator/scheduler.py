"""
Reversion scheduler for the MINAS Orchestrator (Use Case 2: scheduled QoS).

An intent with an enforcement window (`intents.window_end`) needs its policy
reverted once the window closes, without waiting for a new operator intent.
Reverting is a deterministic, time-triggered action — it does not need LLM
reasoning — so this polls the database directly and calls the domain agents'
revert tools over MCP (revert_qos / revert_resources), bypassing the
Orchestrator's own ReAct loop entirely.

Started as a daemon thread from main.py when the orchestrator runs in service
mode. Not used in one-shot CLI mode (`python main.py "<intent>"`), since a
single CLI invocation exits before any window would elapse.

Env:
  SCHEDULER_INTERVAL_SECONDS (default 15) — how often to poll for due intents
"""

from __future__ import annotations

import os
import threading
import time

from db import get_db_conn
from mcp_common import call_remote_tool
from tools import CN_NSSMF_URL, RAN_NSSMF_URL

POLL_INTERVAL_SECONDS = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "15"))


def _due_intents() -> list[tuple[int, int]]:
    """Intents whose enforcement window has closed but haven't been reverted yet."""
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, sst
              FROM intents
             WHERE window_end IS NOT NULL
               AND window_end <= NOW()
               AND status IN ('applied', 'degraded')
            """
        )
        return cur.fetchall()


def _mark_status(intent_id: int, status: str) -> None:
    conn = get_db_conn()
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE intents SET status = %s WHERE id = %s", (status, intent_id))


def _send_revert(url: str, intent_id: int, tool_name: str, sst: int) -> bool:
    """Call a domain agent's revert tool over MCP. `tool_name` is the MCP
    tool exposed by that agent: 'revert_qos' (CN) or 'revert_resources' (RAN)."""
    res = call_remote_tool(url, tool_name, {"intent_id": intent_id, "sst": sst})
    if res.get("error"):
        print(f"[scheduler] {url} {tool_name} intent={intent_id} FAILED: {res['error']}")
        return False
    print(f"[scheduler] {url} {tool_name} intent={intent_id} -> {res.get('result')}")
    return True


def _revert_one(intent_id: int, sst: int) -> None:
    print(f"[scheduler] window expired for intent {intent_id} (sst={sst}) — reverting")

    cn_ok  = _send_revert(CN_NSSMF_URL,  intent_id, "revert_qos",       sst)
    ran_ok = _send_revert(RAN_NSSMF_URL, intent_id, "revert_resources", sst)

    if cn_ok and ran_ok:
        _mark_status(intent_id, "reverted")
    else:
        # Leave status untouched so the next poll retries. revert_qos /
        # revert_resources are no-ops when there is nothing active left to
        # revert, so retrying is safe.
        print(f"[scheduler] intent {intent_id} revert incomplete — will retry next poll")


def _loop() -> None:
    print(f"[scheduler] reversion scheduler started, polling every {POLL_INTERVAL_SECONDS}s")
    while True:
        try:
            for intent_id, sst in _due_intents():
                _revert_one(intent_id, sst)
        except Exception as exc:  # a bad poll must not kill the thread
            print(f"[scheduler] poll error: {exc}")
        time.sleep(POLL_INTERVAL_SECONDS)


def start() -> threading.Thread:
    """Start the scheduler as a daemon thread. Call once, at service startup."""
    thread = threading.Thread(target=_loop, name="revert-scheduler", daemon=True)
    thread.start()
    return thread
