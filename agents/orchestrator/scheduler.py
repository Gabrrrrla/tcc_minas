"""
Window scheduler for the MINAS Orchestrator (Use Case 2: scheduled QoS).

Two time-triggered jobs, polled from the database:
  activation — an intent whose window starts in the future is recorded as
               'scheduled' (orchestrator/tools.py) and nothing touches the
               Core/RAN yet. When window_start arrives, this sends the two
               directives (apply_qos / apply_resources) over MCP and moves the
               intent to applied / degraded / failed from the domains'
               STRUCTURED status.
  reversion  — when window_end passes, calls the deterministic revert tools
               (revert_qos / revert_resources) and marks the intent reverted
               once both confirm.
The Orchestrator's own ReAct loop is not involved in either; the domain
agents still run their ReAct loop to size an activation (as they would for an
immediate intent), but reverts need no LLM at all.

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
from datetime import datetime, timezone

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


def _due_activations() -> list[tuple]:
    """Scheduled intents whose window has opened."""
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, sst, target_thp_mbps, window_end
              FROM intents
             WHERE status = 'scheduled'
               AND window_start <= NOW()
             ORDER BY window_start
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
    status = res.get("status")
    # Success needs a structured confirmation, not merely "no error key":
    # 'reverted' (rows released now) or 'noop' (nothing active left — e.g. a
    # retry after a partial success). Anything else is retried next poll.
    if res.get("error") or status not in ("reverted", "noop"):
        print(f"[scheduler] {url} {tool_name} intent={intent_id} NOT confirmed: {res}")
        return False
    print(f"[scheduler] {url} {tool_name} intent={intent_id} -> {status}")
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


def _activate_one(intent_id: int, sst: int, target_thp_mbps, window_end) -> None:
    if window_end is not None and window_end <= datetime.now(timezone.utc):
        # orchestrator was down for the whole window: applying now would put
        # the policy in force outside the window the operator asked for
        print(f"[scheduler] intent {intent_id}: window already closed before activation — failed")
        _mark_status(intent_id, "failed")
        return

    print(f"[scheduler] window opened for intent {intent_id} (sst={sst}) — activating")
    args = {"intent_id": intent_id, "sst": sst}
    if target_thp_mbps is not None:
        args["target_thp_mbps"] = float(target_thp_mbps)
    cn = call_remote_tool(CN_NSSMF_URL, "apply_qos",
                          {**args, **({"window_end": window_end.isoformat()} if window_end else {})})
    ran = call_remote_tool(RAN_NSSMF_URL, "apply_resources", args)
    statuses = [cn.get("status"), ran.get("status")]
    print(f"[scheduler] intent {intent_id} activation: cn={statuses[0]} ran={statuses[1]}")

    if any(st is None for st in statuses):
        # transport/tool error without a structured outcome: stay 'scheduled'
        # and retry next poll (apply_qos/allocate_prb are idempotent per intent)
        print(f"[scheduler] intent {intent_id} activation incomplete — will retry next poll "
              f"(cn={cn.get('error')}, ran={ran.get('error')})")
        return
    if "failed" in statuses:
        # don't leave half a configuration in force: roll back whichever side
        # did apply, then fail the intent (the reversion job ignores 'failed')
        _send_revert(CN_NSSMF_URL, intent_id, "revert_qos", sst)
        _send_revert(RAN_NSSMF_URL, intent_id, "revert_resources", sst)
        _mark_status(intent_id, "failed")
        return
    _mark_status(intent_id, "degraded" if "degraded" in statuses else "applied")


def _loop() -> None:
    print(f"[scheduler] window scheduler started, polling every {POLL_INTERVAL_SECONDS}s")
    while True:
        try:
            for intent_id, sst, thp, window_end in _due_activations():
                _activate_one(intent_id, sst, thp, window_end)
        except Exception as exc:  # a bad poll must not kill the thread
            print(f"[scheduler] activation poll error: {exc}")
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
