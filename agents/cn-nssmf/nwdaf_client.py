"""
Minimal client for the NWDAF normative interfaces used by the CN-NSSMF.

Reference: 3GPP TS 23.288 (architecture) and TS 29.520 (stage 3).
Two service operations matter to MINAS:
  Nnwdaf_AnalyticsInfo       — synchronous request for an analytics report
  Nnwdaf_EventsSubscription  — subscribe to periodic analytics notifications

get_analytics() now calls the real NWDAF service (agents/nwdaf/), which trains
a RandomForestRegressor on core_kpis history for SLICE_LOAD_LEVEL and returns
mock values for the other analytics IDs (see agents/nwdaf/main.py docstring).
If the NWDAF is unreachable (not deployed, still training, network hiccup),
this degrades to a local mock so the ReAct loop keeps working end to end
instead of failing the whole directive.

subscribe_events() is still a full stub — Nnwdaf_EventsSubscription needs a
callback endpoint on this agent to receive notifications, not built yet.
"""

from __future__ import annotations

import os
import random

import requests

NWDAF_URL = os.getenv("NWDAF_URL", "http://nwdaf:8080")

# Analytics IDs from TS 23.288 §6 relevant to slice resource management
ANALYTICS_IDS = {
    "SLICE_LOAD_LEVEL",      # load level information for a network slice
    "NF_LOAD",               # load of a specific NF / NF set
    "USER_DATA_CONGESTION",  # congestion in the user plane
    "ABNORMAL_BEHAVIOUR",    # anomaly detection
}


def get_analytics(analytics_id: str, sst: int, horizon_seconds: int = 60) -> dict:
    """Nnwdaf_AnalyticsInfo — one-shot predictive analytics request against the
    real NWDAF service. Falls back to a local mock if it can't be reached."""
    if analytics_id not in ANALYTICS_IDS:
        return {"error": f"unknown analytics_id: {analytics_id}"}

    try:
        resp = requests.post(
            f"{NWDAF_URL}/analytics",
            json={"analytics_id": analytics_id, "sst": sst, "horizon_seconds": horizon_seconds},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        predicted = round(random.uniform(0.40, 0.95), 3)
        return {
            "analytics_id": analytics_id,
            "sst": sst,
            "horizon_seconds": horizon_seconds,
            "predicted_load": predicted,   # normalized 0.0 – 1.0
            "confidence": 0.5,
            "source": "mock-fallback",     # nwdaf unreachable -> degraded gracefully
            "error": str(exc),
        }


def subscribe_events(analytics_id: str, sst: int, period_seconds: int = 10) -> dict:
    """Nnwdaf_EventsSubscription — register for periodic notifications.

    TODO: POST a subscription to
    {NWDAF_URL}/nnwdaf-eventssubscription/v1/subscriptions, keep the returned id,
    and expose a callback endpoint for the notifications.
    """
    return {
        "subscription_id": f"mock-sub-{analytics_id.lower()}-{sst}",
        "analytics_id": analytics_id,
        "sst": sst,
        "period_seconds": period_seconds,
        "status": "active",
        "source": "mock",
    }
